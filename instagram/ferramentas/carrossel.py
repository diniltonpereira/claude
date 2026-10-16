"""Gera os slides de um carrossel no visual padrão do @diniltonpereira.

Uso:
  python ferramentas/carrossel.py roteiro.yml posts/<slug>/

O roteiro (YAML) tem este formato:

  etiqueta: "Checklist de governança"      # texto pequeno no alto de cada slide
  livro: "Gestão Jurídica e Estratégica do Terceiro Setor"   # opcional (padrão abaixo)
  capa:
    titulo: "Diretoria com mandato vencido não assina contrato válido"
    subtitulo: "7 perguntas para conferir a governança da sua OSC."
  slides:                                   # 1 a 8 slides de conteúdo
    - titulo: "Mandato em dia"
      texto: "A ata que elegeu a diretoria está registrada?"
      numero: "01"                          # opcional: número grande dourado
      chamada: "LIÇÃO 1"                    # opcional: rótulo dourado acima do título
  fechamento:
    titulo: "Algum “não” na lista?"
    texto: "O livro traz os checklists completos."
    nota: "Confira o texto oficial da norma."   # opcional

Saída: slide01.jpg, slide02.jpg… (JPEG 1080x1350, formato aceito pela API do Instagram).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

W, H, M = 1080, 1350, 90
NAVY, NAVY2 = (20, 33, 61), (30, 47, 84)
CREAM, GOLD, MUTED = (245, 239, 226), (201, 162, 84), (178, 186, 204)

FONTES = Path(__file__).resolve().parent / "fontes"
SERIF_B = FONTES / "texgyrepagella-bold.otf"
SERIF_I = FONTES / "texgyrepagella-italic.otf"
SANS = FONTES / "Inter-Regular.otf"
SANS_M = FONTES / "Inter-Medium.otf"
SANS_SB = FONTES / "Inter-SemiBold.otf"

LIVRO_PADRAO = "Gestão Jurídica e Estratégica do Terceiro Setor"
RODAPE = "@diniltonpereira  ·  diniltonpereira.com.br/livros"
SITE = "www.diniltonpereira.com.br/livros"
WHATS = "WhatsApp (38) 99902-7278"


def f(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def wrap(d, text, font, maxw):
    linhas = []
    for par in text.split("\n"):
        cur = ""
        for w in par.split():
            t = (cur + " " + w).strip()
            if d.textlength(t, font=font) <= maxw:
                cur = t
            else:
                linhas.append(cur)
                cur = w
        linhas.append(cur)
    return linhas


def block(d, y, text, font, color, maxw=W - 2 * M, lh=1.28, x=M):
    for ln in wrap(d, text, font, maxw):
        d.text((x, y), ln, font=font, fill=color)
        y += int(font.size * lh)
    return y


def base(n, total, tag):
    im = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 14], fill=GOLD)
    d.text((M, 70), tag.upper(), font=f(SANS_SB, 26), fill=GOLD)
    num, fn = f"{n}/{total}", f(SANS_M, 26)
    d.text((W - M - d.textlength(num, font=fn), 70), num, font=fn, fill=MUTED)
    d.line([M, H - 130, W - M, H - 130], fill=NAVY2, width=2)
    d.text((M, H - 100), RODAPE, font=f(SANS_M, 28), fill=MUTED)
    return im, d


def capa(n, total, tag, titulo, sub):
    im, d = base(n, total, tag)
    y = block(d, 330, titulo, f(SERIF_B, 84), CREAM, lh=1.14)
    d.rectangle([M, y + 40, M + 120, y + 48], fill=GOLD)
    block(d, y + 90, sub, f(SANS, 38), MUTED)
    d.text((M, H - 220), "Arraste para o lado  →", font=f(SANS_M, 30), fill=GOLD)
    return im


def conteudo(n, total, tag, chamada, titulo, texto, numero=None):
    im, d = base(n, total, tag)
    tf, bf = f(SERIF_B, 76), f(SANS, 46)
    th = len(wrap(d, titulo, tf, W - 2 * M)) * int(76 * 1.16)
    bh = len(wrap(d, texto, bf, W - 2 * M)) * int(46 * 1.42)
    h = (250 if numero else 0) + (75 if chamada else 0) + th + 50 + bh
    y = max(170, 150 + (H - 300 - h) // 2)
    if numero:
        d.text((M, y - 30), numero, font=f(SERIF_B, 220), fill=GOLD)
        y += 250
    if chamada:
        d.text((M, y), chamada, font=f(SANS_SB, 34), fill=GOLD)
        y += 75
    y = block(d, y, titulo, tf, CREAM, lh=1.16)
    block(d, y + 50, texto, bf, MUTED, lh=1.42)
    return im


def fechamento(n, total, tag, titulo, texto, livro, nota=None):
    im = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 14], fill=GOLD)
    d.text((M, 70), tag.upper(), font=f(SANS_SB, 26), fill=(150, 112, 40))
    num, fn = f"{n}/{total}", f(SANS_M, 26)
    d.text((W - M - d.textlength(num, font=fn), 70), num, font=fn, fill=(110, 110, 120))
    y = block(d, 220, titulo, f(SERIF_B, 72), NAVY, lh=1.15)
    y = block(d, y + 30, texto, f(SANS, 38), (60, 66, 84), lh=1.4)
    by = max(y + 50, 760)
    d.rounded_rectangle([M, by, W - M, by + 400], radius=24, fill=NAVY)
    d.text((M + 50, by + 45), "O LIVRO", font=f(SANS_SB, 26), fill=GOLD)
    yy = block(d, by + 90, livro, f(SERIF_B, 46), CREAM, maxw=W - 2 * M - 100, x=M + 50, lh=1.15)
    d.text((M + 50, yy + 25), SITE, font=f(SANS_SB, 34), fill=GOLD)
    d.text((M + 50, yy + 80), WHATS, font=f(SANS_M, 32), fill=CREAM)
    if nota:
        block(d, by + 430, nota, f(SERIF_I, 30), (90, 96, 112), lh=1.3)
    d.text((M, H - 100), RODAPE, font=f(SANS_M, 28), fill=(90, 96, 112))
    return im


def gerar(roteiro: dict, destino: Path) -> list[str]:
    tag = roteiro["etiqueta"]
    slides = roteiro["slides"]
    if not 1 <= len(slides) <= 8:
        raise ValueError("Use de 1 a 8 slides de conteúdo (o carrossel tem no máximo 10 itens).")
    total = len(slides) + 2
    imgs = [capa(1, total, tag, roteiro["capa"]["titulo"], roteiro["capa"].get("subtitulo", ""))]
    for i, s in enumerate(slides, 2):
        imgs.append(conteudo(i, total, tag, s.get("chamada"), s["titulo"], s["texto"], s.get("numero")))
    fc = roteiro["fechamento"]
    imgs.append(fechamento(total, total, tag, fc["titulo"], fc["texto"],
                           roteiro.get("livro", LIVRO_PADRAO), fc.get("nota")))
    destino.mkdir(parents=True, exist_ok=True)
    nomes = []
    for i, im in enumerate(imgs, 1):
        nome = f"slide{i:02d}.jpg"
        im.save(destino / nome, "JPEG", quality=92, optimize=True)
        nomes.append(nome)
    return nomes


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    roteiro = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
    nomes = gerar(roteiro, Path(sys.argv[2]))
    print("\n".join(f"  - {n}" for n in nomes))
