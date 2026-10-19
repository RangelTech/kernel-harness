#!/usr/bin/env python3
"""Gera um grafico de barras com linha de media movel em PNG. Uso: grafico.py [saida.png]."""
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # sem tela
import matplotlib.pyplot as plt  # noqa: E402


def reais(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def grafico(meses: list[str], valores: list[float], saida: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    barras = ax.bar(meses, valores, color="#2563eb")
    ax.bar_label(barras, labels=[reais(v) for v in valores], padding=3, fontsize=8)
    if len(valores) >= 3:
        media = [sum(valores[max(0, i - 2): i + 1]) / len(valores[max(0, i - 2): i + 1])
                 for i in range(len(valores))]
        ax.plot(meses, media, color="#f59e0b", marker="o", label="média móvel (3 meses)")
        ax.legend()
    ax.set_title("Vendas por mês")
    ax.set_xlabel("Mês")
    ax.set_ylabel("Faturamento (R$)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    saida.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(saida, dpi=150)
    plt.close(fig)
    return saida


if __name__ == "__main__":
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get("OUT_DIR", "/workspace/out")) / "grafico.png"
    print(grafico(["Jan", "Fev", "Mar", "Abr", "Mai"], [1200.0, 1850.5, 1500.0, 2100.0, 2400.75], destino))
