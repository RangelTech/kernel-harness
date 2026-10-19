#!/usr/bin/env python3
"""Gera um PDF A4 com titulo, paragrafo e tabela. Uso: gerar_pdf.py [saida.pdf]."""
import os
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def gerar(saida: Path, titulo: str, texto: str, linhas: list[list[str]]) -> Path:
    saida.parent.mkdir(parents=True, exist_ok=True)
    estilos = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(saida), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm, title=titulo)
    tabela = Table(linhas, repeatRows=1)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    doc.build([Paragraph(titulo, estilos["Title"]), Paragraph(texto, estilos["BodyText"]),
               Spacer(1, 0.6 * cm), tabela])
    return saida


if __name__ == "__main__":
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get("OUT_DIR", "/workspace/out")) / "relatorio.pdf"
    print(gerar(destino, "Relatório de vendas", "Resumo do período com os valores consolidados.",
                [["Mês", "Pedidos", "Total (R$)"], ["Janeiro", "12", "1.200,00"], ["Fevereiro", "18", "1.850,50"]]))
