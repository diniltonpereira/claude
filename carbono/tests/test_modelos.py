"""Modelos editáveis em Word: ETP, Termo de Referência e relatório de emissões."""
import io

import pytest
from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from fastapi.testclient import TestClient

from plataforma import demo, modelos
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, conectar


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))
    caminho = str(tmp_path / "demo.db")
    demo.executar(caminho)
    c = conectar(caminho)
    inv = c.execute("SELECT * FROM inventario WHERE status='aprovado' ORDER BY id LIMIT 1").fetchone()
    ped = c.execute("SELECT * FROM pedido ORDER BY id LIMIT 1").fetchone()
    elab = c.execute("SELECT id FROM usuario WHERE papel='elaborador' AND organizacao_id=?", (ped["orgao_id"],)).fetchone()[0]
    return {"c": c, "caminho": caminho, "inv": inv, "ped": ped, "elab": elab}


def texto(conteudo: bytes) -> tuple[str, Document]:
    doc = Document(io.BytesIO(conteudo))
    partes = [p.text for p in doc.paragraphs]
    partes += [c.text for t in doc.tables for r in t.rows for c in r.cells]
    return "\n".join(partes), doc


def destacados(doc) -> list[str]:
    return [r.text for p in doc.paragraphs for r in p.runs if r.font.highlight_color == WD_COLOR_INDEX.YELLOW]


def test_etp_tem_os_incisos_obrigatorios_e_numeros_do_inventario(cenario):
    from plataforma.mrv import totais
    t = totais(cenario["c"], cenario["inv"]["id"])
    txt, doc = texto(modelos.etp_compensacao(cenario["c"], cenario["inv"]["id"]))
    assert txt.startswith("MINUTA") and "art. 53" in txt
    for inciso in ("(art. 18, § 1º, I)", "(art. 18, § 1º, IV)", "(art. 18, § 1º, VI)", "(art. 18, § 1º, VIII)",
                   "(art. 18, § 1º, XIII)"):              # os que o § 2º torna obrigatórios
        assert inciso in txt
    escopo1 = modelos._num(t["escopo1"])
    assert escopo1 in txt and "Quantidade a adquirir" in txt
    assert any("plano de contratações anual" in d for d in destacados(doc))
    assert "carbono neutro" not in txt.lower()


def test_etp_so_para_inventario_aprovado(cenario):
    c, inv = cenario["c"], cenario["inv"]
    rasc = c.execute("INSERT INTO inventario (organizacao_id, ano, elaborado_por) VALUES (?, 2099, ?)",
                     (inv["organizacao_id"], inv["elaborado_por"])).lastrowid
    with pytest.raises(ErroNegocio, match="aprovado"):
        modelos.etp_compensacao(c, rasc)
    txt, _ = texto(modelos.relatorio_inventario(c, rasc))                # o relatório sai, com aviso
    assert "ainda não aprovado" in txt


def test_tr_segue_as_alineas_do_art_6_xxiii(cenario):
    txt, doc = texto(modelos.tr_pedido(cenario["c"], cenario["elab"], cenario["ped"]["id"]))
    for alinea in ("a) Definição do objeto", "b) Fundamentação", "c) Descrição da solução", "d) Requisitos",
                   "e) Modelo de execução", "f) Modelo de gestão", "g) Critérios de medição", "h) Forma e critérios",
                   "i) Estimativa do valor", "j) Adequação orçamentária"):
        assert alinea in txt
    assert modelos._brl(cenario["ped"]["valor_total"]) in txt
    assert "não exige aquisição por plataforma" in txt                 # minuta neutra
    assert len(destacados(doc)) >= 5


def test_relatorio_traz_fatores_resultados_e_trilha(cenario):
    txt, _ = texto(modelos.relatorio_inventario(cenario["c"], cenario["inv"]["id"]))
    assert "MINUTA de relatório" in txt and "art. 53" not in txt
    assert "Escopo 1" in txt and "Faixa do operador" in txt and "e-0" not in txt   # fatores sem notação científica
    assert "aprovado" in txt and "carbono neutro\" sem compensação integral" in txt


def test_rotas_e_controle_de_acesso(cenario):
    app = criar_app(cenario["caminho"])
    cli = TestClient(app)
    c, inv, ped = cenario["c"], cenario["inv"], cenario["ped"]
    dono = c.execute("SELECT id FROM usuario WHERE organizacao_id=? AND papel='elaborador'", (inv["organizacao_id"],)).fetchone()[0]
    estranho = c.execute("SELECT id FROM usuario WHERE papel='elaborador' AND organizacao_id <> ?",
                         (inv["organizacao_id"],)).fetchone()[0]
    H = lambda u: {"X-Usuario-Id": str(u)}  # noqa: E731
    for rota in (f"/inventarios/{inv['id']}/etp-compensacao.docx", f"/inventarios/{inv['id']}/relatorio.docx"):
        r = cli.get(rota, headers=H(dono))
        assert r.status_code == 200 and "wordprocessingml" in r.headers["content-type"] and r.content[:2] == b"PK"
        assert cli.get(rota, headers=H(estranho)).status_code == 403
    r = cli.get(f"/pedidos/{ped['id']}/tr.docx", headers=H(cenario["elab"]))
    assert r.status_code == 200 and 'filename="termo-de-referencia' in r.headers["content-disposition"]
