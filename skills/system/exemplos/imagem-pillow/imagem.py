#!/usr/bin/env python3
"""Operacoes comuns com Pillow. Uso: imagem.py [pasta_de_saida]."""
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def fonte(tamanho: int):
    for caminho in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(caminho, tamanho)
        except OSError:
            continue
    return ImageFont.load_default()


def redimensionar(origem: Path, destino: Path, largura_max: int = 800) -> Path:
    img = ImageOps.exif_transpose(Image.open(origem))  # corrige rotacao de foto de celular
    img.thumbnail((largura_max, largura_max))          # mantem a proporcao
    if destino.suffix.lower() in (".jpg", ".jpeg"):
        img = img.convert("RGB")
    img.save(destino)
    return destino


def marca_dagua(origem: Path, destino: Path, texto: str) -> Path:
    img = Image.open(origem).convert("RGBA")
    camada = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    d.text((img.width - 10, img.height - 10), texto, font=fonte(max(12, img.width // 20)),
           fill=(255, 255, 255, 160), anchor="rb")
    Image.alpha_composite(img, camada).convert("RGB").save(destino)
    return destino


def criar_cartao(destino: Path, titulo: str, subtitulo: str, tamanho=(1080, 1080)) -> Path:
    img = Image.new("RGB", tamanho, "#1e3a8a")
    d = ImageDraw.Draw(img)
    d.text((tamanho[0] // 2, tamanho[1] // 2 - 40), titulo, font=fonte(90), fill="white", anchor="mm")
    d.text((tamanho[0] // 2, tamanho[1] // 2 + 60), subtitulo, font=fonte(48), fill="#93c5fd", anchor="mm")
    img.save(destino)
    return destino


if __name__ == "__main__":
    pasta = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get("OUT_DIR", "/workspace/out"))
    pasta.mkdir(parents=True, exist_ok=True)
    cartao = criar_cartao(pasta / "cartao.png", "Promoção", "Frete grátis hoje")
    print(redimensionar(cartao, pasta / "cartao_pequeno.jpg", 300))
    print(marca_dagua(cartao, pasta / "cartao_marca.jpg", "Minha Loja"))
