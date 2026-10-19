#!/usr/bin/env python3
"""Google Sheets, Gmail e Drive por REST. Token OAuth em GOOGLE_TOKEN; ou service account em GCP_SA_JSON."""
import json
import os
import sys

import requests


def token_service_account(escopos: list[str] | None = None) -> str:
    """Troca o JSON da service account (variavel GCP_SA_JSON) por um token de acesso."""
    import google.auth.transport.requests
    from google.oauth2 import service_account

    info = json.loads(os.environ["GCP_SA_JSON"])
    cred = service_account.Credentials.from_service_account_info(
        info, scopes=escopos or ["https://www.googleapis.com/auth/spreadsheets.readonly"])
    cred.refresh(google.auth.transport.requests.Request())
    return cred.token


def _get(url: str, params: dict | None = None, token: str | None = None) -> dict:
    tk = token or os.environ["GOOGLE_TOKEN"]
    r = requests.get(url, params=params, headers={"Authorization": "Bearer " + tk}, timeout=30)
    r.raise_for_status()
    return r.json()


def ler_planilha(planilha_id: str, intervalo: str = "A1:Z100", token: str | None = None) -> list[list[str]]:
    d = _get(f"https://sheets.googleapis.com/v4/spreadsheets/{planilha_id}/values/{intervalo}", token=token)
    return d.get("values", [])


def escrever_planilha(planilha_id: str, intervalo: str, linhas: list[list], token: str | None = None) -> dict:
    tk = token or os.environ["GOOGLE_TOKEN"]
    r = requests.put(f"https://sheets.googleapis.com/v4/spreadsheets/{planilha_id}/values/{intervalo}",
                     params={"valueInputOption": "USER_ENTERED"}, json={"values": linhas},
                     headers={"Authorization": "Bearer " + tk}, timeout=30)
    r.raise_for_status()
    return r.json()


def listar_emails(max_resultados: int = 5, consulta: str = "in:inbox") -> list[dict]:
    lista = _get("https://gmail.googleapis.com/gmail/v1/users/me/messages",
                 {"maxResults": max_resultados, "q": consulta}).get("messages", [])
    out = []
    for m in lista:
        d = _get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}",
                 {"format": "metadata", "metadataHeaders": ["From", "Subject"]})
        h = {x["name"]: x["value"] for x in d["payload"]["headers"]}
        out.append({"id": m["id"], "de": h.get("From"), "assunto": h.get("Subject")})
    return out


def listar_arquivos(max_resultados: int = 10) -> list[dict]:
    return _get("https://www.googleapis.com/drive/v3/files",
                {"pageSize": max_resultados, "fields": "files(id,name,mimeType,modifiedTime)"}).get("files", [])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(ler_planilha(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "A1:Z100"))
    else:
        for f in listar_arquivos():
            print(f["name"], f["id"])
