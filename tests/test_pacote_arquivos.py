"""Pacote do template com arquivos de qualquer tipo (spec 10, H-2), offline."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from harness.profiles import decode_package_file
from tests.conftest import final
from tests.test_broker import last_result, tool

PDF = b"%PDF-1.7\n\xe2\xe3\xcf\xd3\n1 0 obj << /Item /Pizza >> endobj\n%%EOF\n"
XLSX = b"PK\x03\x04\x00\xff\xfe binario qualquer"


def asset(path: str, data: bytes, *, sha: str | None = None) -> dict:
    return {"path": path, "content": base64.b64encode(data).decode(), "encoding": "base64",
            "sha256": sha or hashlib.sha256(data).hexdigest(), "mime": "application/octet-stream"}


def package(extra=None) -> list[dict]:
    return [
        {"path": "AGENTE.md", "content": "Voce e o atendente da pizzaria."},
        {"path": "skills/triagem/SKILL.md",
         "content": "---\nname: triagem\ndescription: Triagem\n---\nCorpo.\n"},
        asset("arquivos/cardapio.pdf", PDF),
        asset("arquivos/precos/tabela.xlsx", XLSX),
        *(extra or []),
    ]


def pkg_dir(rig, version="v1") -> Path:
    return Path(rig.settings.tenant_skills_root) / "tpl1" / version


def test_decode_package_file_text_binary_and_checks():
    assert decode_package_file({"path": "a.md", "content": "olá"}) == "olá".encode()
    assert decode_package_file(asset("arquivos/a.pdf", PDF)) == PDF
    for bad in (asset("arquivos/a.pdf", PDF, sha="0" * 64),
                {"path": "arquivos/a", "content": "@@nao-e-base64@@", "encoding": "base64"},
                {"path": "arquivos/a", "content": None}):
        try:
            decode_package_file(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


async def test_binary_files_are_written_to_disk_byte_for_byte(rig):
    ev = await rig.send(rig.body(files=package()))
    assert final(ev).type == "done"
    base = pkg_dir(rig)
    assert (base / "arquivos" / "cardapio.pdf").read_bytes() == PDF
    assert (base / "arquivos" / "precos" / "tabela.xlsx").read_bytes() == XLSX
    assert (base / "AGENTE.md").read_text(encoding="utf-8").startswith("Voce e o atendente")
    cur = Path(rig.settings.tenant_skills_root) / "tpl1" / "current"
    assert (cur / "arquivos" / "cardapio.pdf").read_bytes() == PDF  # caminho estavel para o agente


async def test_instructions_list_the_package_files(rig):
    ev = await rig.send(rig.body(files=package()))
    assert final(ev).type == "done"
    ins = rig.hermes[0].requests[-1]["body"]["instructions"]
    assert "Arquivos do template" in ins
    assert "arquivos/cardapio.pdf" in ins and "arquivos/precos/tabela.xlsx" in ins
    assert "package_file" in ins
    # sem arquivos no pacote a secao nao aparece
    ev = await rig.send(rig.body(version="v-sem", files=[
        {"path": "AGENTE.md", "content": "x"}]))
    assert "Arquivos do template" not in rig.hermes[0].requests[-1]["body"]["instructions"]


async def test_replacing_the_file_in_a_new_version_changes_what_the_agent_sees(rig):
    novo = PDF.replace(b"Pizza", b"Hamburguer")
    ev = await rig.send(rig.body(version="v1", files=package()))
    assert final(ev).type == "done"
    b2 = rig.body(version="v2", files=package([asset("arquivos/cardapio.pdf", novo)])[:2]
                  + [asset("arquivos/cardapio.pdf", novo)])
    b2["skills"]["tenant_package"]["sha256"] = "pkg-v2"
    ev = await rig.send(b2)
    assert final(ev).type == "done"
    assert (pkg_dir(rig, "v2") / "arquivos" / "cardapio.pdf").read_bytes() == novo
    assert (pkg_dir(rig, "v1") / "arquivos" / "cardapio.pdf").read_bytes() == PDF
    cur = Path(rig.settings.tenant_skills_root) / "tpl1" / "current" / "arquivos" / "cardapio.pdf"
    assert cur.read_bytes() == novo


async def test_package_from_url_carries_base64_files_too(rig):
    url = "https://pkg.fake/binario"
    rig.backend.packages[url] = {"files": package()}
    b = rig.body(version="vurl")
    b["skills"]["tenant_package"] = {"sha256": "sha-url-bin", "url": url, "files": None}
    ev = await rig.send(b)
    assert final(ev).type == "done"
    assert (pkg_dir(rig, "vurl") / "arquivos" / "cardapio.pdf").read_bytes() == PDF


async def test_corrupted_binary_is_refused_and_nothing_is_published(rig):
    bad = rig.body(version="vbad", files=package([asset("arquivos/x.pdf", PDF, sha="f" * 64)]))
    ev = await rig.send(bad)
    assert final(ev).type == "error"
    assert not pkg_dir(rig, "vbad").exists()


async def test_env_upload_copies_package_file_to_workspace(rig):
    kw = {"files": package()}
    res = await last_result(rig, tool("rt_env_upload", {"package_file": "arquivos/cardapio.pdf"}),
                            **kw)
    assert res["ok"], res
    assert res["result"] == {"path": "/workspace/pacote/cardapio.pdf", "size": len(PDF)}
    assert rig.ext.rm_files["/workspace/pacote/cardapio.pdf"] == PDF

    res = await last_result(rig, tool("rt_env_upload", {"package_file": "arquivos/cardapio.pdf",
                                                         "path": "/workspace/in/menu.pdf"}), **kw)
    assert rig.ext.rm_files["/workspace/in/menu.pdf"] == PDF

    res = await last_result(rig, tool("rt_env_upload", {"package_file": "*"}), **kw)
    assert res["result"]["count"] == 2
    assert rig.ext.rm_files["/workspace/pacote/precos/tabela.xlsx"] == XLSX


async def test_env_upload_package_file_rejects_escape_and_missing(rig):
    kw = {"files": package()}
    for wanted in ("arquivos/../AGENTE.md", "../../etc/passwd", "AGENTE.md",
                   "arquivos/nao-existe.pdf", "/etc/passwd"):
        res = await last_result(rig, tool("rt_env_upload", {"package_file": wanted}), **kw)
        assert not res["ok"] and res["error"]["code"] == "invalid_args", (wanted, res)
    assert not [p for p in rig.ext.rm_files if "AGENTE" in p or "passwd" in p]


async def test_env_upload_needs_attachment_or_package_file(rig):
    res = await last_result(rig, tool("rt_env_upload", {}), files=package())
    assert res["error"]["code"] == "invalid_args"


async def test_env_upload_without_package_files_explains(rig):
    res = await last_result(rig, tool("rt_env_upload", {"package_file": "*"}))
    assert not res["ok"] and "arquivos" in res["error"]["message"]
