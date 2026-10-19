#!/usr/bin/env python3
"""Cliente generico de API REST: Bearer, retry, paginacao, OAuth client credentials.

Rodar o arquivo faz uma demo do retry contra um servidor local (sem rede externa).
"""
import os
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


def sessao(token_env: str = "API_TOKEN") -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "agente-rt/1.0"
    if os.environ.get(token_env):
        s.headers["Authorization"] = "Bearer " + os.environ[token_env]
    return s


def com_retry(s: requests.Session, metodo: str, url: str, tentativas: int = 4, espera: float = 1.0,
              **kw) -> requests.Response:
    """Repete em 429 e 5xx com espera exponencial; erros 4xx (exceto 429) falham na hora."""
    kw.setdefault("timeout", 30)
    ultima: Exception | None = None
    for i in range(tentativas):
        try:
            r = s.request(metodo, url, **kw)
        except (requests.ConnectionError, requests.Timeout) as e:
            ultima = e
        else:
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
                return r
            ultima = requests.HTTPError(f"{r.status_code} {r.text[:200]}")
            retry_after = r.headers.get("Retry-After", "")
            if retry_after.isdigit():
                time.sleep(min(int(retry_after), 60))
                continue
        time.sleep(espera * (2 ** i))
    raise RuntimeError(f"falhou apos {tentativas} tentativas: {ultima}")


def paginar(s: requests.Session, url: str, params: dict | None = None, chave: str = "items",
            por_pagina: int = 100, limite_paginas: int = 200) -> Iterator[dict]:
    """Pagina por `page`/`per_page`; se a resposta trouxer `next` (URL de cursor), segue o cursor."""
    params = dict(params or {}, per_page=por_pagina)
    pagina = 1
    while pagina <= limite_paginas:  # teto: nunca um laco sem fim
        r = com_retry(s, "GET", url, params={**params, "page": pagina}).json()
        itens = r if isinstance(r, list) else r.get(chave, [])
        yield from itens
        proximo = None if isinstance(r, list) else r.get("next")
        if proximo:
            url, params, pagina = proximo, {}, pagina + 1
            continue
        if len(itens) < por_pagina:
            return
        pagina += 1


def token_oauth() -> str:
    """OAuth2 client credentials: troca id/segredo por um token de acesso (guarde so em memoria)."""
    r = requests.post(os.environ["OAUTH_TOKEN_URL"], timeout=30, data={
        "grant_type": "client_credentials",
        "client_id": os.environ["OAUTH_CLIENT_ID"],
        "client_secret": os.environ["OAUTH_CLIENT_SECRET"]})
    r.raise_for_status()
    return r.json()["access_token"]


def _demo() -> None:
    estado = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            estado["n"] += 1
            self.send_response(503 if estado["n"] < 3 else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"items": [1, 2, 3]}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(list(paginar(sessao(), url, por_pagina=100)), "tentativas:", estado["n"])
    srv.shutdown()


if __name__ == "__main__":
    _demo()
