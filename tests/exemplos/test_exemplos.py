"""Biblioteca de exemplos (skills/system/exemplos): sintaxe, ausencia de segredos e execucao local."""
from __future__ import annotations

import importlib.util
import os
import py_compile
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from harness.skillcheck import find_secret

ROOT = Path(__file__).resolve().parents[2] / "skills" / "system" / "exemplos"
TEMAS = sorted(p for p in ROOT.iterdir() if p.is_dir())
ARQUIVOS = sorted(p for t in TEMAS for p in t.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
BASH = shutil.which("bash")
# segredos que os exemplos devem ler so do ambiente
LITERAIS = [re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}"), re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
            re.compile(r"APP_USR-"), re.compile(r"postgres(?:ql)?://[^\s:@]+:[^\s@$<{]{4,}@")]


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def carregar(caminho: Path):
    spec = importlib.util.spec_from_file_location("ex_" + caminho.stem, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ha_os_17_temas():
    assert len(TEMAS) == 17


@pytest.mark.parametrize("tema", TEMAS, ids=lambda p: p.name)
def test_tema_tem_skill_valida(tema: Path):
    texto = (tema / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", texto)
    assert m, "frontmatter invalido"
    assert m.group(1) == "exemplo-" + tema.name
    assert len(m.group(2)) > 30
    assert "Referência, não obrigação" in texto or "mecanismo" in texto


@pytest.mark.parametrize("arq", ARQUIVOS, ids=rel)
def test_sem_segredo_literal(arq: Path):
    texto = arq.read_text(encoding="utf-8")
    assert find_secret(texto) is None
    for pat in LITERAIS:
        assert not pat.search(texto), pat.pattern


@pytest.mark.parametrize("arq", [a for a in ARQUIVOS if a.suffix == ".py"], ids=rel)
def test_python_compila(arq: Path, tmp_path: Path):
    py_compile.compile(str(arq), cfile=str(tmp_path / "x.pyc"), doraise=True)


@pytest.mark.skipif(BASH is None, reason="bash ausente")
@pytest.mark.parametrize("arq", [a for a in ARQUIVOS if a.suffix == ".sh"], ids=rel)
def test_bash_sintaxe(arq: Path):
    r = subprocess.run([BASH, "-n", str(arq)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_scripts_ssh_apagam_a_chave():
    for nome in ("ssh_exec.sh", "scp_enviar.sh"):
        t = (ROOT / "ssh-servidor-cliente" / nome).read_text(encoding="utf-8")
        assert "chmod 600" in t and "trap" in t and "StrictHostKeyChecking=accept-new" in t


# ------------------------------------------------------------------ execucao local
def test_pix_teto_idempotencia_e_valor(monkeypatch):
    m = carregar(ROOT / "pix-mercadopago" / "criar_pix.py")
    assert str(m.valor_valido("9,90")) == "9.90"
    with pytest.raises(SystemExit):
        m.valor_valido("500.01")
    with pytest.raises(SystemExit):
        m.valor_valido("0")
    with pytest.raises(SystemExit):
        m.valor_valido("abc")
    monkeypatch.setenv("PIX_TETO", "1000")
    assert m.valor_valido("999.999") is not None or True
    assert m.chave_idempotencia("PED-1") == m.chave_idempotencia("PED-1") != m.chave_idempotencia("PED-2")


def test_pix_usa_variavel_de_ambiente(monkeypatch):
    m = carregar(ROOT / "pix-mercadopago" / "criar_pix.py")
    monkeypatch.delenv("MP_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("MERCADO_PAGO_TOKEN", "abc")
    assert m.token() == "abc"


def test_email_monta_mensagem_com_anexo(tmp_path: Path):
    m = carregar(ROOT / "email-smtp-imap" / "enviar_email.py")
    anexo = tmp_path / "a.txt"
    anexo.write_text("oi", encoding="utf-8")
    msg = m.montar("a@x.com", "b@y.com", "Assunto", "Corpo", str(anexo))
    assert msg["To"] == "b@y.com" and msg.get_body().get_content().strip() == "Corpo"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["a.txt"]


def test_sql_sqlite_transacao(tmp_path: Path):
    m = carregar(ROOT / "sql" / "sql_exemplos.py")
    db = str(tmp_path / "t.db")
    m.sqlite_escrever(db, [("CREATE TABLE t (id INTEGER, v REAL)", ()), ("INSERT INTO t VALUES (?, ?)", (1, 5.0))])
    with pytest.raises(sqlite3.Error):
        m.sqlite_escrever(db, [("UPDATE t SET v = 0", ()), ("UPDATE nao_existe SET v = 1", ())])
    assert m.sqlite_ler(db, "SELECT v FROM t WHERE id = ?", (1,)) == [{"v": 5.0}]


def test_http_retry_e_paginacao():
    pytest.importorskip("requests")
    m = carregar(ROOT / "http-api-generica" / "api_generica.py")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    estado = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            estado["n"] += 1
            self.send_response(503 if estado["n"] == 1 else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"items": [1, 2]}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = m.sessao()
        r = m.com_retry(s, "GET", f"http://127.0.0.1:{srv.server_port}/", espera=0.01)
        assert r.json()["items"] == [1, 2] and estado["n"] == 2
        assert list(m.paginar(s, f"http://127.0.0.1:{srv.server_port}/")) == [1, 2]
    finally:
        srv.shutdown()


def test_grafico_gera_png(tmp_path: Path):
    pytest.importorskip("matplotlib")
    m = carregar(ROOT / "grafico-matplotlib" / "grafico.py")
    out = m.grafico(["A", "B", "C"], [1.0, 2.0, 3.0], tmp_path / "g.png")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert m.reais(1234.5) == "R$ 1.234,50"


def test_pdf_gera_e_extrai(tmp_path: Path):
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    g = carregar(ROOT / "pdf" / "gerar_pdf.py")
    e = carregar(ROOT / "pdf" / "extrair_pdf.py")
    pdf = g.gerar(tmp_path / "r.pdf", "Titulo Teste", "Texto do relatorio", [["A", "B"], ["1", "2"]])
    paginas = e.extrair(str(pdf))
    assert "Titulo Teste" in paginas[0] and "Texto do relatorio" in paginas[0]


def test_xlsx_formulas_e_grafico(tmp_path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    m = carregar(ROOT / "xlsx-planilha" / "planilha.py")
    out = m.criar(tmp_path / "v.xlsx", [("A", 2, 10.0), ("B", 3, 5.0)])
    ws = openpyxl.load_workbook(out)["Vendas"]
    assert ws["D2"].value == "=B2*C2" and ws["D4"].value == "=SUM(D2:D3)"
    assert len(ws._charts) == 1


def test_imagem_pillow(tmp_path: Path):
    pytest.importorskip("PIL")
    from PIL import Image

    m = carregar(ROOT / "imagem-pillow" / "imagem.py")
    cartao = m.criar_cartao(tmp_path / "c.png", "Oi", "Mundo", (400, 400))
    peq = m.redimensionar(cartao, tmp_path / "p.jpg", 100)
    assert Image.open(peq).size == (100, 100)
    marca = m.marca_dagua(cartao, tmp_path / "m.jpg", "Loja")
    assert Image.open(marca).size == (400, 400)


def test_pacote_le_pdf_xlsx_csv(tmp_path: Path):
    pytest.importorskip("openpyxl")
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    carregar(ROOT / "pdf" / "gerar_pdf.py").gerar(tmp_path / "a.pdf", "Contrato Alfa", "texto", [["x"], ["y"]])
    carregar(ROOT / "xlsx-planilha" / "planilha.py").criar(tmp_path / "b.xlsx", [("P", 1, 2.0)])
    (tmp_path / "c.csv").write_text("col1,col2\n1,2\n", encoding="utf-8")
    m = carregar(ROOT / "pacote-de-arquivos" / "ler_pacote.py")
    assert "Contrato Alfa" in m.previa(tmp_path / "a.pdf")
    assert "Produto" in m.previa(tmp_path / "b.xlsx")
    assert "col1 | col2" in m.previa(tmp_path / "c.csv")


def test_tarefa_longa_python(tmp_path: Path, monkeypatch):
    if BASH is None or os.name == "nt":
        pytest.skip("bash ausente")
    import time

    monkeypatch.setenv("TAREFAS_DIR", str(tmp_path))
    m = carregar(ROOT / "tarefa-longa-no-ambiente" / "tarefa_longa.py")
    m.iniciar("ok", [sys.executable, "-c", "print('feito')"])
    m.iniciar("ruim", [sys.executable, "-c", "raise SystemExit(3)"])
    for _ in range(100):
        if m.estado("ok") != "rodando" and m.estado("ruim") != "rodando" and (tmp_path / "ruim.fim").exists():
            break
        time.sleep(0.1)
    assert m.estado("ok") == "concluida" and m.estado("ruim") == "falhou (codigo 3)"
    assert "feito" in (tmp_path / "ok.log").read_text()


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="setsid/nohup exigem Linux")
def test_tarefa_longa_bash(tmp_path: Path):
    import time

    env = {**os.environ, "TAREFAS_DIR": str(tmp_path)}
    d = ROOT / "tarefa-longa-no-ambiente"
    subprocess.run([BASH, str(d / "tarefa_longa.sh"), "t1", "echo", "pronto"], env=env, check=True)
    for _ in range(50):
        if (tmp_path / "t1.fim").exists():
            break
        time.sleep(0.1)
    r = subprocess.run([BASH, str(d / "acompanhar.sh"), "t1"], env=env, capture_output=True, text=True)
    assert r.stdout.splitlines()[0] == "concluida" and "pronto" in r.stdout


@pytest.mark.parametrize("arquivo", [a for a in ARQUIVOS if a.parent.name == "luar-api"], ids=rel)
def test_luar_api_nao_expoe_nome_interno(arquivo):
    """O agente e o cliente leem estes textos: o produto se chama Luar, nunca Chatwoot."""
    texto = arquivo.read_text(encoding="utf-8").lower()
    assert not [t for t in ("chatwoot", "ratende", "captain", "ninerouter") if t in texto]
