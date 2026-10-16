"""Leitura e validação dos posts em posts/<slug>/post.yml."""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "posts"
STATE_FILE = ROOT / "state" / "publicados.json"

TIPOS = {"imagem", "carrossel", "reel"}
STATUS = {"rascunho", "agendado", "aguardando_video"}
IMG_EXT = {".jpg", ".jpeg"}
VID_EXT = {".mp4", ".mov"}
MAX_LEGENDA = 2200
MAX_HASHTAGS = 30


@dataclass
class Post:
    slug: str
    pasta: Path
    titulo: str
    tipo: str
    status: str
    publicar_em: dt.datetime
    midia: list[str]
    legenda: str
    hashtags: list[str] = field(default_factory=list)
    capa: str | None = None

    @property
    def texto(self) -> str:
        tags = " ".join(h if h.startswith("#") else f"#{h}" for h in self.hashtags)
        return f"{self.legenda.strip()}\n\n{tags}".strip()

    def vencido(self, agora: dt.datetime) -> bool:
        return self.status == "agendado" and self.publicar_em <= agora


def _data(valor) -> dt.datetime:
    if isinstance(valor, dt.datetime):
        d = valor
    else:
        d = dt.datetime.fromisoformat(str(valor))
    if d.tzinfo is None:
        raise ValueError("publicar_em precisa de fuso horário, ex.: 2026-10-21T12:00:00-03:00")
    return d


def carregar(pasta: Path) -> Post:
    dados = yaml.safe_load((pasta / "post.yml").read_text(encoding="utf-8")) or {}
    return Post(
        slug=pasta.name,
        pasta=pasta,
        titulo=str(dados.get("titulo", pasta.name)),
        tipo=str(dados.get("tipo", "")).lower(),
        status=str(dados.get("status", "rascunho")).lower(),
        publicar_em=_data(dados.get("publicar_em")),
        midia=[str(m) for m in (dados.get("midia") or [])],
        legenda=str(dados.get("legenda", "")),
        hashtags=[str(h) for h in (dados.get("hashtags") or [])],
        capa=dados.get("capa"),
    )


def todos(posts_dir: Path = POSTS_DIR) -> list[Post]:
    if not posts_dir.exists():
        return []
    pastas = sorted(p for p in posts_dir.iterdir() if (p / "post.yml").exists())
    return sorted((carregar(p) for p in pastas), key=lambda p: p.publicar_em)


def validar(post: Post) -> list[str]:
    erros: list[str] = []
    if post.tipo not in TIPOS:
        erros.append(f"tipo '{post.tipo}' inválido (use {', '.join(sorted(TIPOS))})")
    if post.status not in STATUS:
        erros.append(f"status '{post.status}' inválido (use {', '.join(sorted(STATUS))})")
    if len(post.texto) > MAX_LEGENDA:
        erros.append(f"legenda com {len(post.texto)} caracteres (máximo {MAX_LEGENDA})")
    if len(post.hashtags) > MAX_HASHTAGS:
        erros.append(f"{len(post.hashtags)} hashtags (máximo {MAX_HASHTAGS})")

    # Reel ainda sem vídeo pode existir como pendência.
    if post.status == "aguardando_video":
        return erros

    n = len(post.midia)
    if post.tipo == "imagem" and n != 1:
        erros.append("post de imagem precisa de exatamente 1 arquivo")
    if post.tipo == "carrossel" and not 2 <= n <= 10:
        erros.append(f"carrossel precisa de 2 a 10 arquivos (tem {n})")
    if post.tipo == "reel" and n != 1:
        erros.append("Reel precisa de exatamente 1 vídeo")

    proporcao_capa = None
    for nome in post.midia + ([post.capa] if post.capa else []):
        arq = post.pasta / nome
        ext = arq.suffix.lower()
        if not arq.exists():
            erros.append(f"arquivo não encontrado: {nome}")
            continue
        if post.tipo == "reel" and nome != post.capa:
            if ext not in VID_EXT:
                erros.append(f"{nome}: Reel aceita .mp4 ou .mov")
            continue
        if ext not in IMG_EXT:
            erros.append(f"{nome}: a API do Instagram só aceita JPEG (rode 'preparar')")
            continue
        with Image.open(arq) as im:
            w, h = im.size
        r = w / h
        if not 0.8 - 0.005 <= r <= 1.91 + 0.005:
            erros.append(f"{nome}: proporção {w}x{h} fora do intervalo 4:5 a 1,91:1")
        if w > 1440:
            erros.append(f"{nome}: largura {w}px acima de 1440px")
        if post.tipo == "carrossel":
            if proporcao_capa is None:
                proporcao_capa = r
            elif abs(r - proporcao_capa) > 0.01:
                erros.append(f"{nome}: proporção diferente do primeiro slide (o Instagram recorta pelo primeiro)")
    return erros


def preparar(pasta: Path, qualidade: int = 92) -> list[str]:
    """Converte PNG/WebP da pasta em JPEG e atualiza a lista 'midia' do post.yml."""
    convertidos = []
    yml = pasta / "post.yml"
    dados = yaml.safe_load(yml.read_text(encoding="utf-8"))
    nova = []
    for nome in dados.get("midia") or []:
        arq = pasta / nome
        if arq.suffix.lower() in {".png", ".webp"} and arq.exists():
            destino = arq.with_suffix(".jpg")
            with Image.open(arq) as im:
                im.convert("RGB").save(destino, "JPEG", quality=qualidade, optimize=True)
            arq.unlink()
            convertidos.append(destino.name)
            nova.append(destino.name)
        else:
            nova.append(nome)
    if convertidos:
        texto = yml.read_text(encoding="utf-8")
        for antigo, novo in zip(dados["midia"], nova):
            texto = texto.replace(antigo, novo)
        yml.write_text(texto, encoding="utf-8")
    return convertidos


def _pasta_no_repo(post: Post) -> str:
    """Caminho da pasta do post a partir da raiz do repositório (ex.: instagram/posts/<slug>)."""
    raiz = Path(os.environ.get("GITHUB_WORKSPACE") or ROOT).resolve()
    try:
        return post.pasta.resolve().relative_to(raiz).as_posix()
    except ValueError:
        return f"posts/{post.slug}"


def url_publica(post: Post, nome: str) -> str:
    base = os.environ.get("MEDIA_BASE_URL")
    caminho = "/".join(quote(p) for p in _pasta_no_repo(post).split("/")) + f"/{quote(nome)}"
    if base:
        return f"{base.rstrip('/')}/{caminho}"
    repo = os.environ.get("GITHUB_REPOSITORY")
    ref = os.environ.get("GITHUB_SHA", "main")
    if not repo:
        raise RuntimeError("Defina MEDIA_BASE_URL ou rode dentro do GitHub Actions.")
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{caminho}"


# ---------- Estado (o que já foi publicado) ----------
def ler_estado(arquivo: Path = STATE_FILE) -> dict:
    if arquivo.exists():
        return json.loads(arquivo.read_text(encoding="utf-8"))
    return {}


def gravar_estado(estado: dict, arquivo: Path = STATE_FILE) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(estado, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
