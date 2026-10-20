"""Pensando, subagentes e formato de saida no stream (itens C e D da spec 10, 15.2)."""
from __future__ import annotations

from harness.dispatcher import FORMAT_RULES, build_instructions
from harness.hermes_client import HermesEvent
from harness.security import Redactor
from harness.translate import EventTranslator, friendly_thinking
from tests.conftest import final
from tests.test_contracts import assert_contract


def ev(name: str, **data) -> HermesEvent:
    return HermesEvent(1, name, {"event": name, **data})


def test_thinking_is_summarised_and_hides_internal_names():
    txt = friendly_thinking("<think>Vou chamar rt_sql_read pelo Hermes\n\n  agora</think>")
    assert txt == "Vou chamar uma ferramenta pelo o sistema agora"
    assert "rt_" not in txt and "ermes" not in txt
    long = friendly_thinking("palavra " * 200)
    assert len(long) <= 281 and long.endswith("…")


def test_translator_emits_thinking_once_per_distinct_text():
    tr = EventTranslator()
    out = tr.translate(ev("reasoning.available", text="Analisando o pedido do cliente"))
    assert out.events == [("thinking", {"text": "Analisando o pedido do cliente"})]
    assert tr.translate(ev("reasoning.available", text="Analisando o pedido do cliente")
                        ).events == []
    assert tr.translate(ev("reasoning.available", text="   ")).events == []


def subs(sig):
    return [d for t, d in sig.events if t == "subagents"]


def test_subagent_counter_uses_batch_size_and_closes_on_complete():
    tr = EventTranslator()
    s1 = tr.translate(ev("subagent.start", goal="a", subagent_id="s1", delegation_id="d",
                         task_count=3))
    assert subs(s1) == [{"active": 1, "total": 3}]
    s2 = tr.translate(ev("subagent.start", goal="b", subagent_id="s2", delegation_id="d",
                         task_count=3))
    assert subs(s2) == [{"active": 2, "total": 3}]
    c1 = tr.translate(ev("subagent.complete", goal="a", subagent_id="s1", delegation_id="d"))
    assert subs(c1) == [{"active": 1, "total": 3}]
    # a delegacao termina: nenhum filho segue ativo, mesmo sem 'complete' de cada um
    end = tr.translate(ev("tool.completed", tool="delegate_task", preview="ok"))
    assert subs(end) == [{"active": 0, "total": 3}]


def test_subagent_without_ids_still_counts_and_balances():
    tr = EventTranslator()
    assert subs(tr.translate(ev("subagent.start", goal="x"))) == [{"active": 1, "total": 1}]
    assert subs(tr.translate(ev("subagent.start", goal="y"))) == [{"active": 2, "total": 2}]
    assert subs(tr.translate(ev("subagent.complete", goal="x"))) == [{"active": 1, "total": 2}]
    assert subs(tr.translate(ev("subagent.complete", goal="y"))) == [{"active": 0, "total": 2}]


def test_delegate_tool_has_friendly_label():
    sig = EventTranslator().translate(ev("tool.started", tool="delegate_task", preview=""))
    label = sig.events[0][1]["label"]
    assert label == "Dividindo o trabalho em subtarefas"


def test_instructions_carry_the_format_rule():
    red = Redactor.from_credentials({})
    md = build_instructions("# A", [], [], red)
    assert FORMAT_RULES["markdown"] in md
    wa = build_instructions("# A", [], [], red, output_format="whatsapp")
    assert "*negrito*" in wa and "NÃO use tabelas" in wa and "hífen" in wa
    assert FORMAT_RULES["markdown"] not in wa
    tx = build_instructions("# A", [], [], red, output_format="text")
    assert "texto simples" in tx


async def test_stream_has_thinking_subagents_and_done_format(rig):
    body = rig.body(message=(
        "[[reason:Preciso dividir em tres partes]] [[sub:parte um|s1|3]] [[sub:parte dois|s2|3]]"
        " [[sub:parte tres|s3|3]] [[subdone:s1|parte um]] [[say:pronto]]"))
    events = await rig.send(body)
    assert final(events).type == "done"
    assert_contract(events)
    assert final(events).data["format"] == "markdown"
    assert ("thinking", {"text": "Preciso dividir em tres partes"}) in [
        (e.type, e.data) for e in events]
    counts = [e.data for e in events if e.type == "subagents"]
    assert counts[0] == {"active": 1, "total": 3}
    assert {"active": 3, "total": 3} in counts and counts[-1] == {"active": 2, "total": 3}
    assert "Markdown completo" in rig.hermes[0].requests[-1]["body"]["instructions"]


async def test_whatsapp_format_goes_to_instructions_and_done(rig):
    body = rig.body(message="[[say:oi]]")
    body["manifest"]["output_format"] = "whatsapp"
    events = await rig.send(body)
    assert final(events).data["format"] == "whatsapp"
    assert_contract(events)
    assert "WhatsApp" in rig.hermes[0].requests[-1]["body"]["instructions"]


async def test_unknown_format_is_rejected_by_the_contract(rig):
    body = rig.body(message="[[say:oi]]")
    body["manifest"]["output_format"] = "html"
    r = (await rig.send(body))[0]
    assert r.type == "http" and r.data["status"] == 422 and r.data["code"] == "invalid_request"


def test_output_format_helper_falls_back_to_markdown():
    from harness.dispatcher import output_format_of

    assert output_format_of({"output_format": "html"}) == "markdown"
    assert output_format_of(None) == "markdown"
    assert output_format_of({"output_format": "text"}) == "text"
