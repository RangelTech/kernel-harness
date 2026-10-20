"""Ferramenta longa (rt_env_exec de minutos) nao conta como silencio do modelo."""
import time

from harness.runctx import RunContext


def _ctx() -> RunContext:
    return RunContext(run_id="r", tenant_id="t", user_id="u", chat_id="c", template_id="tp",
                      version_id="v", channel="chat", actor={}, session_id="s")


def test_silencio_nao_anda_com_ferramenta_em_andamento():
    ctx = _ctx()
    antes = ctx.silence_now()
    ctx.tool_begin()
    ctx._busy_started -= 300  # simula 5 min dentro da ferramenta
    durante = ctx.silence_now()
    ctx.tool_end()
    depois = ctx.silence_now()
    assert durante - antes < 1
    assert depois - antes < 1
    assert ctx.busy_depth == 0 and ctx._busy_started is None


def test_silencio_volta_a_andar_fora_da_ferramenta():
    ctx = _ctx()
    ctx.tool_begin()
    ctx.tool_end()
    a = ctx.silence_now()
    ctx.started_at -= 0  # sem efeito; o relogio monotonic segue
    time.sleep(0.05)
    assert ctx.silence_now() - a >= 0.04


def test_chamadas_aninhadas_somam_uma_vez():
    ctx = _ctx()
    ctx.tool_begin()
    ctx.tool_begin()
    ctx.tool_end()
    assert ctx.busy_depth == 1 and ctx._busy_started is not None
    ctx.tool_end()
    assert ctx.busy_depth == 0


def test_nota_confirm_actions_em_lista():
    from harness.dispatcher import build_instructions, confirm_actions_note

    assert confirm_actions_note(True) is None and confirm_actions_note([]) is None
    nota = confirm_actions_note(["chart", "pix_create"])
    assert "rt_confirm" in nota and "`chart`" in nota
    from harness.security import Redactor

    assert nota in build_instructions("# Agente", [], [], Redactor([]), None, nota)
