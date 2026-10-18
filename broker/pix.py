"""Pix / Mercado Pago (spec 6.3 e 6.4): valor Decimal, idempotencia por (chat, referencia,
valor), teto, confirmacao autenticada antes de criar, QR como artefato.

A entrega de produto digital NUNCA e decidida aqui: so o webhook oficial do provedor libera.
"""
from __future__ import annotations

import base64
import hashlib
import re
import uuid
from decimal import Decimal, InvalidOperation

import httpx

from broker.artifacts import put_artifact
from broker.errors import ToolError
from harness.runctx import RunContext

_REF = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
STATUS_MAP = {
    "pending": "pending", "in_process": "pending", "authorized": "pending",
    "approved": "paid", "rejected": "failed", "cancelled": "cancelled",
    "refunded": "refunded", "charged_back": "refunded", "in_mediation": "pending",
}


def parse_amount(raw) -> Decimal:
    """Decimal exato com no maximo 2 casas; nunca arredonda silenciosamente."""
    if isinstance(raw, bool) or raw is None:
        raise ToolError("invalid_args", "Informe o valor da cobrança.")
    text = str(raw).strip().replace("R$", "").strip()
    if len(text) > 20:
        raise ToolError("invalid_args", "Valor inválido.")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        d = Decimal(text)
    except InvalidOperation as exc:
        raise ToolError("invalid_args", "Valor inválido.") from exc
    if not d.is_finite() or d <= 0:
        raise ToolError("invalid_args", "O valor deve ser maior que zero.")
    try:
        q = d.quantize(Decimal("0.01"))
    except InvalidOperation as exc:  # expoente absurdo (1e400)
        raise ToolError("invalid_args", "Valor fora do intervalo permitido.") from exc
    if d != q:
        raise ToolError("invalid_args", "O valor deve ter no máximo 2 casas decimais.")
    return q


def idempotency_key(tenant_id: str, chat_id: str, reference: str, amount: Decimal) -> str:
    raw = f"{tenant_id}|{chat_id}|{reference}|{amount.quantize(Decimal('0.01'))}"
    return str(uuid.UUID(hashlib.sha256(raw.encode()).hexdigest()[:32]))


def brl(amount: Decimal) -> str:
    whole, frac = f"{amount:.2f}".split(".")
    parts = []
    while len(whole) > 3:
        parts.insert(0, whole[-3:])
        whole = whole[:-3]
    parts.insert(0, whole)
    return "R$ " + ".".join(parts) + "," + frac


def _payment_cred(svc, ctx: RunContext) -> dict:
    if not ctx.granted("mercado_pago"):
        raise ToolError("forbidden_target", "Este agente não pode usar o Mercado Pago.")
    pay = (ctx.credentials or {}).get("payment") or {}
    if not pay.get("access_token"):
        raise ToolError("unavailable", "Mercado Pago não está configurado para esta empresa.")
    return pay


def _limit(svc, ctx: RunContext) -> Decimal:
    configured = ctx.limits.get("pix_max_amount")
    cap = Decimal(str(svc.s.pix_max_amount))
    if configured is not None:
        try:
            cap = min(cap, Decimal(str(configured)))
        except InvalidOperation:
            pass
    return cap


async def _mp(svc, pay: dict, method: str, path: str, *, json_body=None, params=None,
              idem: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {pay['access_token']}"}
    if idem:
        headers["X-Idempotency-Key"] = idem
    try:
        r = await svc.http.request(method, f"{svc.s.mercado_pago_api}{path}", json=json_body,
                                   params=params, headers=headers, timeout=20.0)
    except httpx.HTTPError as exc:
        raise ToolError("unavailable", "O Mercado Pago não respondeu. Tente novamente.") from exc
    if r.status_code >= 400:
        raise ToolError("tool_error", f"Mercado Pago recusou a operação (http {r.status_code}).")
    return r.json()


async def create(svc, ctx: RunContext, args: dict):
    pay = _payment_cred(svc, ctx)
    amount = parse_amount(args.get("amount"))
    reference = str(args.get("reference") or args.get("reference_id") or "").strip()
    if not _REF.match(reference):
        raise ToolError("invalid_args", "Informe uma referência estável do pedido "
                                        "(letras, números, ponto, hífen; até 64).")
    if amount > _limit(svc, ctx):
        raise ToolError("guardrail", f"Valor acima do teto permitido para este agente "
                                     f"({brl(_limit(svc, ctx))}).")
    key = idempotency_key(ctx.tenant_id, ctx.chat_id, reference, amount)
    if key in ctx.pix_results:
        return {**ctx.pix_results[key], "reused": True}

    existing = await svc.backend.lookup_charge(ctx.run_id, ctx.run_token, reference)
    if existing:
        try:
            same = Decimal(str(existing.get("amount"))) == amount
        except InvalidOperation:
            same = False
        if not same:
            raise ToolError("guardrail", "Esta referência já tem uma cobrança com outro valor. "
                                         "Use outra referência.")
        out = {"payment_id": str(existing.get("payment_id")), "reference": reference,
               "amount": str(amount), "status": STATUS_MAP.get(existing.get("status"), "pending"),
               "reused": True,
               "note": "Cobrança já existente: não crie outra para exibir novamente."}
        ctx.pix_results[key] = out
        return out

    description = str(args.get("description") or "Cobrança")[:140]
    await svc.confirm(ctx, kind="pix_create", risk="high",
                      summary=f"Criar cobrança Pix de {brl(amount)} — {description} (ref. {reference})",
                      details={"amount": str(amount), "reference": reference,
                               "description": description})

    body = {"transaction_amount": float(amount), "description": description,
            "payment_method_id": "pix",
            "payer": {"email": str(args.get("payer_email") or "pagador@example.com")},
            "external_reference": reference}
    if pay.get("notification_url"):
        body["notification_url"] = pay["notification_url"]
    data = await _mp(svc, pay, "POST", "/v1/payments", json_body=body, idem=key)

    td = ((data.get("point_of_interaction") or {}).get("transaction_data")) or {}
    payment_id = str(data.get("id"))
    status = STATUS_MAP.get(data.get("status"), "pending")
    out = {"payment_id": payment_id, "reference": reference, "amount": str(amount),
           "status": status, "copy_paste": td.get("qr_code"),
           "note": "Cobrança criada. A entrega só pode ocorrer após a confirmação oficial do "
                   "pagamento pela plataforma."}
    qr_b64 = td.get("qr_code_base64")
    if qr_b64:
        try:
            png = base64.b64decode(qr_b64, validate=False)
            out["qr_code_artifact"] = await put_artifact(
                svc, ctx, kind="image", title=f"QR Code Pix {brl(amount)}", data=png,
                extension="png", content_type="image/png")
        except ToolError:
            out["qr_code_artifact"] = None
    await svc.backend.record_charge(ctx.run_id, ctx.run_token, {
        "tenant_id": ctx.tenant_id, "chat_id": ctx.chat_id, "user_id": ctx.user_id,
        "payment_id": payment_id, "reference_id": reference, "amount": str(amount),
        "status": data.get("status"), "idempotency_key": key})
    ctx.pix_results[key] = out
    return out


async def payment_status(svc, ctx: RunContext, args: dict):
    pay = _payment_cred(svc, ctx)
    payment_id = str(args.get("payment_id") or "").strip()
    reference = str(args.get("reference") or "").strip()
    if not payment_id and reference:
        found = await svc.backend.lookup_charge(ctx.run_id, ctx.run_token, reference)
        if not found:
            raise ToolError("invalid_args", "Nenhuma cobrança encontrada para esta referência.")
        payment_id = str(found.get("payment_id"))
    if not re.fullmatch(r"\d{3,30}", payment_id):
        raise ToolError("invalid_args", "Informe o payment_id ou a referência da cobrança.")
    data = await _mp(svc, pay, "GET", f"/v1/payments/{payment_id}")
    status = STATUS_MAP.get(data.get("status"), "pending")
    await svc.backend.record_charge(ctx.run_id, ctx.run_token, {
        "tenant_id": ctx.tenant_id, "chat_id": ctx.chat_id, "user_id": ctx.user_id,
        "payment_id": payment_id, "reference_id": data.get("external_reference") or reference,
        "amount": str(data.get("transaction_amount")), "status": data.get("status")})
    return {"payment_id": payment_id, "status": status,
            "amount": str(data.get("transaction_amount")),
            "reference": data.get("external_reference"),
            "note": "Status informativo. Liberação de produto só pelo aviso oficial da plataforma."}


async def mp_read(svc, ctx: RunContext, args: dict):
    pay = _payment_cred(svc, ctx)
    params = {"limit": max(1, min(int(args.get("limit") or 20), 50)), "sort": "date_created",
              "criteria": "desc"}
    if args.get("reference"):
        params["external_reference"] = str(args["reference"])[:64]
    if args.get("status"):
        params["status"] = str(args["status"])[:20]
    data = await _mp(svc, pay, "GET", "/v1/payments/search", params=params)
    items = [{"payment_id": str(p.get("id")), "status": STATUS_MAP.get(p.get("status"), "pending"),
              "amount": str(p.get("transaction_amount")), "reference": p.get("external_reference"),
              "created": p.get("date_created"), "description": p.get("description")}
             for p in (data.get("results") or [])]
    return {"payments": items, "count": len(items)}
