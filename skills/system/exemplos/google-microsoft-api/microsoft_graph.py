#!/usr/bin/env python3
"""Microsoft Graph com token de acesso em MS_TOKEN."""
import os

import requests

BASE = "https://graph.microsoft.com/v1.0"


def _get(caminho: str, params: dict | None = None) -> dict:
    r = requests.get(BASE + caminho, params=params, timeout=30,
                     headers={"Authorization": "Bearer " + os.environ["MS_TOKEN"]})
    r.raise_for_status()
    return r.json()


def eu() -> dict:
    return _get("/me")


def listar_emails(n: int = 5) -> list[dict]:
    d = _get("/me/messages", {"$top": n, "$select": "subject,from,receivedDateTime",
                              "$orderby": "receivedDateTime desc"})
    return [{"assunto": m["subject"], "de": m["from"]["emailAddress"]["address"],
             "data": m["receivedDateTime"]} for m in d["value"]]


def eventos(n: int = 10) -> list[dict]:
    d = _get("/me/events", {"$top": n, "$select": "subject,start,end", "$orderby": "start/dateTime"})
    return [{"titulo": e["subject"], "inicio": e["start"]["dateTime"], "fim": e["end"]["dateTime"]}
            for e in d["value"]]


if __name__ == "__main__":
    print(eu().get("displayName"))
    for e in eventos():
        print(e["inicio"], e["titulo"])
