"""docker/patches/hermes_idle_cpu.py: ancoras exatas, tudo-ou-nada, env com padrao do upstream."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "hermes_idle_cpu", Path(__file__).parent.parent / "docker" / "patches" / "hermes_idle_cpu.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _tree(tmp_path: Path, drop: str | None = None, dup: str | None = None) -> Path:
    """Arvore falsa do Hermes: cada arquivo contem as ancoras originais dele."""
    files: dict[str, list[str]] = {}
    for rel, old, _new in mod.PATCHES:
        files.setdefault(rel, []).append(old)
    for rel, olds in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        body = []
        for old in olds:
            if old == drop:
                continue
            body.append(old)
            if old == dup:
                body.append(old)
        p.write_text("\n".join(body), encoding="utf-8")
    return tmp_path


def test_aplica_todas_as_ancoras(tmp_path):
    root = _tree(tmp_path)
    changed = mod.apply(root)
    assert {p.relative_to(root).as_posix() for p in changed} == {r for r, _, _ in mod.PATCHES}
    run = (root / "gateway/run.py").read_text()
    assert "RT_HERMES_CRON" in run and "RT_HERMES_MCP_RECONCILE" in run
    assert "RT_HERMES_MEM_TRIM_COOLDOWN_S" in run
    assert "RT_HERMES_HANDOFF_POLL_S" in (root / "gateway/run_adapters.py").read_text()
    assert "RT_HERMES_LOOP_POLL_S" in (root / "gateway/run_goals.py").read_text()
    assert "RT_HERMES_ORPHAN_SWEEP_S" in (root / "tools/async_delegation.py").read_text()
    assert "RT_HERMES_CRON" in (root / "cron/scheduler_provider.py").read_text()


def test_padrao_sem_variavel_e_o_do_upstream():
    # cada leitura de env tem como padrao o valor original do Hermes
    txt = "\n".join(new for _, _, new in mod.PATCHES)
    assert '"RT_HERMES_CRON", "1"' in txt
    assert '"RT_HERMES_MCP_RECONCILE", "1"' in txt
    assert '"RT_HERMES_HANDOFF_POLL_S", "2.0"' in txt
    assert '"RT_HERMES_LOOP_POLL_S", "15"' in txt
    assert '"RT_HERMES_ORPHAN_SWEEP_S", "30"' in txt
    assert '"RT_HERMES_MEM_TRIM_COOLDOWN_S", "60"' in txt


def test_ancora_ausente_nao_grava_nada(tmp_path):
    drop = mod.PATCHES[-1][1]  # a do async_delegation
    root = _tree(tmp_path, drop=drop)
    antes = {p: p.read_text() for p in root.rglob("*.py")}
    with pytest.raises(SystemExit, match="0x"):
        mod.apply(root)
    assert {p: p.read_text() for p in root.rglob("*.py")} == antes


def test_ancora_duplicada_falha(tmp_path):
    dup = mod.PATCHES[0][1]
    root = _tree(tmp_path, dup=dup)
    with pytest.raises(SystemExit, match="2x"):
        mod.apply(root)


def test_trechos_novos_sao_python_valido():
    import ast
    import textwrap

    for rel, _old, new in mod.PATCHES:
        src = textwrap.dedent(new)
        if src.startswith(("async def", "ORPHAN")):
            wrapped = src
        else:  # trecho de corpo de funcao
            wrapped = "def _f():" + chr(10) + textwrap.indent(src, "    ")
        last = wrapped.rstrip().splitlines()[-1]
        if last.endswith(":"):  # def/if sem corpo no recorte: fecha com pass
            wrapped += " " * (len(last) - len(last.lstrip()) + 4) + "pass" + chr(10)
        ast.parse(wrapped, filename=rel)
