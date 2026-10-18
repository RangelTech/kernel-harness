"""Servicos externos falsos via httpx.MockTransport: Mercado Pago, armazenamento de artefatos
(PUT presign) e uma API de terceiros para o rt_http. Registra todas as requisicoes."""
from __future__ import annotations

import base64
import json
import re
import uuid
from decimal import Decimal

import httpx

TINY_PNG = base64.b64encode(
    base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEA"
                     "WZyIpgAAAABJRU5ErkJggg==")).decode()


class FakeExternals:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.payments: dict[str, dict] = {}
        self.by_idem: dict[str, str] = {}
        self.uploads: dict[str, bytes] = {}
        self.mp_fail = False
        self.partner_calls: list[dict] = []

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def mp_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "api.mercadopago.com"]

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        host, path = req.url.host, req.url.path
        if host == "api.mercadopago.com":
            return self._mp(req, path)
        if host == "storage.fake" and req.method == "PUT":
            self.uploads[path.rsplit("/", 1)[-1]] = req.content
            return httpx.Response(200)
        if host == "api.partner.test":
            self.partner_calls.append({"method": req.method, "path": path,
                                       "headers": dict(req.headers),
                                       "body": req.content.decode() if req.content else ""})
            return httpx.Response(200, json={"echo_auth": req.headers.get("authorization"),
                                             "path": path})
        return httpx.Response(404, json={"error": "unknown host " + str(host)})

    def _mp(self, req: httpx.Request, path: str) -> httpx.Response:
        if req.headers.get("authorization", "").split(" ")[-1] == "":
            return httpx.Response(401, json={"message": "no token"})
        if self.mp_fail:
            return httpx.Response(500, json={"message": "boom"})
        if req.method == "POST" and path == "/v1/payments":
            idem = req.headers.get("x-idempotency-key", "")
            if idem in self.by_idem:  # idempotencia real do provedor
                return httpx.Response(200, json=self.payments[self.by_idem[idem]])
            body = json.loads(req.content)
            pid = str(int(uuid.uuid4().int % 10**10) + 1000000000)
            pay = {"id": int(pid), "status": "pending",
                   "transaction_amount": body["transaction_amount"],
                   "external_reference": body.get("external_reference"),
                   "description": body.get("description"),
                   "date_created": "2026-10-18T10:00:00.000-03:00",
                   "point_of_interaction": {"transaction_data": {
                       "qr_code": "00020126PIXCOPIAECOLA" + pid, "qr_code_base64": TINY_PNG}}}
            self.payments[pid] = pay
            self.by_idem[idem] = pid
            return httpx.Response(201, json=pay)
        m = re.fullmatch(r"/v1/payments/(\d+)", path)
        if req.method == "GET" and m:
            pay = self.payments.get(m.group(1))
            return httpx.Response(200, json=pay) if pay else httpx.Response(404, json={})
        if req.method == "GET" and path == "/v1/payments/search":
            return httpx.Response(200, json={"results": list(self.payments.values())})
        return httpx.Response(404, json={})

    def pay(self, payment_id: str, status: str = "approved") -> None:
        self.payments[payment_id]["status"] = status

    def amounts(self) -> list[Decimal]:
        return [Decimal(str(p["transaction_amount"])) for p in self.payments.values()]
