---
name: exemplo-imagem-pillow
description: Exemplos com Pillow: redimensionar, recortar, converter formato, marca d'água e montar uma imagem com texto.
---

# Exemplo: imagens com Pillow

Referência, não obrigação. Instalação: `pip install --user pillow`.

## Arquivo
- `imagem.py`: funções `redimensionar`, `marca_dagua`, `criar_cartao`. Rodar `python3 imagem.py [PASTA]` gera um cartão de exemplo, uma miniatura e uma versão com marca d'água em `/workspace/out` (ou na pasta indicada).

## Cuidados
- `ImageOps.exif_transpose` antes de redimensionar fotos de celular (corrige rotação).
- JPEG não tem transparência: converta para `RGB` antes de salvar como `.jpg`.
- Para texto com acentos use uma fonte TrueType (`DejaVuSans.ttf` costuma existir em `/usr/share/fonts/truetype/dejavu/`); sem ela o Pillow usa a fonte padrão embutida.
- Para gerar imagem por IA, use `rt_image_generate`, não o Pillow.
