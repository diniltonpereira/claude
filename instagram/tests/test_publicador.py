"""Testes com uma API do Instagram simulada (nenhuma chamada real à Meta)."""
import datetime as dt
from pathlib import Path

import pytest
from PIL import Image

from publisher import posts as P
from publisher.__main__ import publicar_post
from publisher.instagram import InstagramClient, InstagramError


class Resp:
    def __init__(self, body, code=200):
        self.body, self.status_code, self.text = body, code, str(body)

    def json(self):
        return self.body


class FakeSession:
    """Imita graph.instagram.com e guarda as chamadas feitas."""

    def __init__(self, status_seq=None, falhar_em=None):
        self.calls = []
        self.n = 0
        self.status_seq = list(status_seq or ["FINISHED"])
        self.falhar_em = falhar_em

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, dict(data)))
        if self.falhar_em and self.falhar_em in url:
            return Resp({"error": {"message": "Invalid image url"}}, 400)
        self.n += 1
        if url.endswith("/media_publish"):
            return Resp({"id": f"MEDIA{self.n}"})
        return Resp({"id": f"C{self.n}"})

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, dict(params)))
        if params.get("fields") == "status_code":
            s = self.status_seq.pop(0) if len(self.status_seq) > 1 else self.status_seq[0]
            return Resp({"status_code": s})
        if params.get("fields") == "permalink":
            return Resp({"permalink": "https://www.instagram.com/p/ABC/"})
        return Resp({})


def cliente(sess):
    return InstagramClient("17841400000000000", "TOKEN", session=sess, sleep=lambda s: None)


def criar_post(tmp: Path, slug="2026-10-21-teste", tipo="carrossel", n=3, ext=".jpg",
               tamanho=(1080, 1350), status="agendado", quando="2026-10-21T12:00:00-03:00"):
    d = tmp / "posts" / slug
    d.mkdir(parents=True)
    midia = []
    for i in range(1, n + 1):
        nome = f"slide{i:02d}{ext}"
        if ext in (".jpg", ".png"):
            Image.new("RGB", tamanho, (20, 33, 61)).save(d / nome)
        else:
            (d / nome).write_bytes(b"\x00")
        midia.append(nome)
    (d / "post.yml").write_text(
        f'titulo: "Teste"\ntipo: {tipo}\nstatus: {status}\npublicar_em: "{quando}"\n'
        "midia:\n" + "".join(f"  - {m}\n" for m in midia) +
        "legenda: |\n  Texto do post.\nhashtags: [terceirosetor, OSC]\n", encoding="utf-8")
    return P.carregar(d)


@pytest.fixture(autouse=True)
def repo_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "dinilton/instagram")
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.delenv("MEDIA_BASE_URL", raising=False)


def test_carrossel_fluxo_completo(tmp_path):
    post = criar_post(tmp_path)
    sess = FakeSession()
    media_id = publicar_post(cliente(sess), post)
    assert media_id.startswith("MEDIA")
    posts_ = [c for c in sess.calls if c[0] == "POST"]
    itens = [c for c in posts_ if c[2].get("is_carousel_item") == "true"]
    assert len(itens) == 3
    assert itens[0][2]["image_url"] == (
        "https://raw.githubusercontent.com/dinilton/instagram/abc123/posts/2026-10-21-teste/slide01.jpg")
    carrossel = [c for c in posts_ if c[2].get("media_type") == "CAROUSEL"][0]
    assert carrossel[2]["children"] == "C1,C2,C3"
    assert "#terceirosetor #OSC" in carrossel[2]["caption"]
    assert posts_[-1][1].endswith("/media_publish")
    assert all(c[1].startswith("https://graph.instagram.com/v25.0/") for c in sess.calls)


def test_reel_espera_processamento(tmp_path):
    post = criar_post(tmp_path, tipo="reel", n=1, ext=".mp4")
    sess = FakeSession(status_seq=["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    publicar_post(cliente(sess), post)
    assert sum(1 for c in sess.calls if c[2].get("fields") == "status_code") == 3
    reel = [c for c in sess.calls if c[2].get("media_type") == "REELS"][0]
    assert reel[2]["video_url"].endswith("/slide01.mp4")


def test_reel_com_erro_nao_publica(tmp_path):
    post = criar_post(tmp_path, tipo="reel", n=1, ext=".mp4")
    sess = FakeSession(status_seq=["ERROR"])
    with pytest.raises(InstagramError):
        publicar_post(cliente(sess), post)
    assert not any(c[1].endswith("/media_publish") for c in sess.calls)


def test_erro_da_api_vira_mensagem_clara(tmp_path):
    post = criar_post(tmp_path)
    with pytest.raises(InstagramError, match="Invalid image url"):
        publicar_post(cliente(FakeSession(falhar_em="/media")), post)


def test_validacao(tmp_path):
    assert P.validar(criar_post(tmp_path, slug="ok")) == []
    png = criar_post(tmp_path, slug="png", ext=".png")
    assert any("JPEG" in e for e in P.validar(png))
    quadrado_demais = criar_post(tmp_path, slug="alto", tamanho=(1080, 1920))
    assert any("proporção" in e for e in P.validar(quadrado_demais))
    um_so = criar_post(tmp_path, slug="um", n=1)
    assert any("2 a 10" in e for e in P.validar(um_so))
    pendente = criar_post(tmp_path, slug="pend", tipo="reel", n=0, status="aguardando_video")
    assert P.validar(pendente) == []


def test_fila_respeita_data_e_status(tmp_path):
    tz = dt.timezone(dt.timedelta(hours=-3))
    post = criar_post(tmp_path)
    assert not post.vencido(dt.datetime(2026, 10, 21, 11, 59, tzinfo=tz))
    assert post.vencido(dt.datetime(2026, 10, 21, 12, 0, tzinfo=tz))
    rasc = criar_post(tmp_path, slug="r", status="rascunho")
    assert not rasc.vencido(dt.datetime(2030, 1, 1, tzinfo=tz))


def test_preparar_converte_png(tmp_path):
    post = criar_post(tmp_path, ext=".png")
    P.preparar(post.pasta)
    novo = P.carregar(post.pasta)
    assert novo.midia == ["slide01.jpg", "slide02.jpg", "slide03.jpg"]
    assert P.validar(novo) == []


def test_media_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_BASE_URL", "https://dinilton.github.io/instagram/")
    post = criar_post(tmp_path)
    assert P.url_publica(post, "slide01.jpg") == (
        "https://dinilton.github.io/instagram/posts/2026-10-21-teste/slide01.jpg")


def test_posts_do_repositorio_sao_validos():
    for post in P.todos():
        assert P.validar(post) == [], post.slug
