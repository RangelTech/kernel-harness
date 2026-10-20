#!/usr/bin/env python3
"""Cliente minimo do Luar. Requer LUAR_URL, LUAR_CTX e LUAR_ACCOUNT_ID no ambiente."""
import os

import requests


class Luar:
    def __init__(self) -> None:
        self.base = f'{os.environ["LUAR_URL"].rstrip("/")}/api/v1/accounts/{os.environ["LUAR_ACCOUNT_ID"]}'
        self.h = {"X-Luar-Context": os.environ["LUAR_CTX"], "Content-Type": "application/json"}

    def _req(self, metodo: str, caminho: str, **kw):
        r = requests.request(metodo, self.base + caminho, headers=self.h, timeout=30, **kw)
        r.raise_for_status()
        return r.json() if r.content else {}

    def conversas(self, status: str = "open", pagina: int = 1) -> list[dict]:
        return self._req("GET", "/conversations", params={"status": status, "page": pagina})["data"]["payload"]

    def quadro(self) -> dict:
        """Etapas do Kanban (id, nome, contagem)."""
        return self._req("GET", "/rt_kanban/board")

    def cards(self, etapa_id: int, status: str = "all") -> list[dict]:
        return self._req("GET", "/rt_kanban/cards", params={"stage_id": etapa_id, "status": status})["items"]

    def mover_card(self, card: dict, etapa_id: int) -> dict:
        """card = item de cards() (traz id e lock_version). Idempotente pela chave."""
        chave = f"mv-{card['id']}-{etapa_id}-{card['lock_version']}"
        return self._req("PATCH", f"/rt_kanban/cards/{card['id']}/move",
                         json={"to_stage_id": etapa_id, "expected_lock_version": card["lock_version"],
                               "idempotency_key": chave})

    def nota_privada(self, conversa_id: int, texto: str) -> dict:
        """Interna: o cliente nao ve. Padrao para registrar o que voce fez."""
        return self._req("POST", f"/conversations/{conversa_id}/messages",
                         json={"content": texto, "message_type": "outgoing", "private": True})

    def etiquetar(self, conversa_id: int, etiquetas: list[str]) -> dict:
        return self._req("POST", f"/conversations/{conversa_id}/labels", json={"labels": etiquetas})

    def responder_cliente(self, conversa_id: int, texto: str) -> dict:
        """So se a skill do template permitir expressamente. Na duvida, use nota_privada."""
        return self._req("POST", f"/conversations/{conversa_id}/messages",
                         json={"content": texto, "message_type": "outgoing", "private": False})


if __name__ == "__main__":
    for c in Luar().conversas()[:10]:
        print(c["id"], c.get("status"), (c.get("meta", {}).get("sender") or {}).get("name"))
