#!/usr/bin/env python3
"""Extrai o texto de um PDF. Uso: extrair_pdf.py arquivo.pdf [max_paginas]."""
import sys

from pypdf import PdfReader


def extrair(caminho: str, max_paginas: int = 50) -> list[str]:
    leitor = PdfReader(caminho)
    return [(p.extract_text() or "").strip() for p in leitor.pages[:max_paginas]]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    paginas = extrair(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 50)
    if not any(paginas):
        print("Sem texto extraivel (provavelmente PDF escaneado).")
    for i, t in enumerate(paginas, 1):
        print(f"--- pagina {i} ---\n{t}")
