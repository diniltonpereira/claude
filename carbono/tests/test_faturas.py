"""Importação de faturas de energia: leitura de PDF e planilha, conferência e lançamento sem duplicar."""
import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from plataforma import anexos, faturas as fa, mrv
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, conectar


def gerar_pdf(linhas: list[str]) -> bytes:
    """PDF mínimo com texto (Helvetica, WinAnsi), como uma fatura digital."""
    esc = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    fluxo = ("BT /F1 10 Tf 40 800 Td 14 TL " + " ".join(f"({esc(l)}) Tj T*" for l in linhas) + " ET").encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Length %d >>\nstream\n" % len(fluxo) + fluxo + b"\nendstream"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % x for x in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


GRUPO_B = ["CEMIG DISTRIBUIÇÃO S.A.", "PREFEITURA MUNICIPAL DE VALE VERDE", "Nº DA INSTALAÇÃO 3012345678",
           "Referente a OUT/2025", "Vencimento 20/11/2025", "Consumo médio diário 41,2 kWh",
           "Consumo kWh 1.234", "Energia Elétrica TUSD kWh 1.234 0,45", "Energia Elétrica TE kWh 1.234 0,31"]


def ler(linhas):
    return fa.ler_fatura(fa.extrair_texto_pdf(gerar_pdf(linhas)))


# ---------------------------------------------------------------- leitura do PDF
def test_fatura_grupo_b_nao_confunde_media_diaria():
    r = ler(GRUPO_B)
    assert (r.uc, r.ano, r.mes, r.kwh, r.confianca) == ("3012345678", 2025, 10, 1234.0, "alta")
    assert "1.234" in r.trechos["consumo"]


def test_fatura_grupo_a_soma_ponta_e_fora_de_ponta():
    r = ler(["Instalação: 3000111222", "Mês de referência: 09/2025",
             "Consumo Ponta 1.200 kWh", "Consumo Fora Ponta 15.300,5 kWh"])
    assert r.kwh == pytest.approx(16500.5) and r.confianca == "media" and any("grupo A" in a for a in r.avisos)


def test_linhas_te_e_tusd_nao_sao_somadas():
    r = ler(["Unidade consumidora 3099988877", "Referente a JAN/2025",
             "Energia Elétrica TUSD kWh 980 0,45", "Energia Elétrica TE kWh 980 0,31"])
    assert r.kwh == 980 and r.confianca == "media"


def test_geracao_distribuida_gera_aviso():
    r = ler(GRUPO_B + ["Energia injetada kWh 300"])
    assert any("geração distribuída" in a for a in r.avisos)


def test_fatura_incompleta_e_pdf_invalido():
    r = ler(["CEMIG", "Consumo kWh 500"])
    assert r.confianca == "baixa" and any("instalação" in a and "mês" in a for a in r.avisos)
    with pytest.raises(ErroNegocio, match="ler o texto"):
        fa.extrair_texto_pdf(b"%PDF-1.4 isto nao e um pdf")


# ---------------------------------------------------------------- planilha
def test_planilha_csv_com_ponto_e_virgula_e_erros_por_linha():
    csv = ("Instalação;Referência;Consumo (kWh)\n3012345678;10/2025;1.234\n3000111222;nov/2025;980,5\n"
           ";10/2025;10\n3000111223;13/2025;10\n").encode()
    linhas = fa.ler_planilha("contas.csv", csv)
    assert [(l["uc"], l["ano"], l["mes"], l["kwh"]) for l in linhas[:2]] == [
        ("3012345678", 2025, 10, 1234.0), ("3000111222", 2025, 11, 980.5)]
    assert "instalação vazia" in linhas[2]["erro"] and "referência" in linhas[3]["erro"]


def test_planilha_xlsx_com_data_e_cabecalho_obrigatorio():
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["UC", "Competência", "Consumo"])
    wb.active.append([3012345678, datetime(2025, 3, 1), 777])
    buf = io.BytesIO(); wb.save(buf)
    assert fa.ler_planilha("x.xlsx", buf.getvalue())[0] | {} == {
        "linha": 2, "uc": "3012345678", "ano": 2025, "mes": 3, "kwh": 777.0, "erro": None}
    with pytest.raises(ErroNegocio, match="colunas"):
        fa.ler_planilha("y.csv", b"nome,valor\na,1\n")


# ---------------------------------------------------------------- análise e lançamento
@pytest.fixture
def s(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))
    c = conectar(str(tmp_path / "f.db"))
    org = c.execute("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Pref','11111111000191','orgao_publico')").lastrowid
    elab = c.execute("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('E','elaborador',?)", (org,)).lastrowid
    hosp = mrv.criar_instalacao(c, elab, org, nome="Hospital", uc_energia="3012345678")
    inv = mrv.criar_inventario(c, elab, org, 2025)
    anexo = anexos.salvar(c, elab, "f.pdf", "application/pdf", gerar_pdf(GRUPO_B))["id"]
    return {"c": c, "elab": elab, "inv": inv, "hosp": hosp, "anexo": anexo, "org": org}


def test_analise_vincula_unidade_e_aponta_duplicadas_e_ano_errado(s):
    itens = [{"uc": "3012345678", "ano": 2025, "mes": 10, "kwh": 1234, "erro": None},
             {"uc": "3012345678", "ano": 2025, "mes": 10, "kwh": 1234, "erro": None},
             {"uc": "3000111222", "ano": 2025, "mes": 10, "kwh": 50, "erro": None},
             {"uc": "3000111222", "ano": 2024, "mes": 12, "kwh": 50, "erro": None}]
    r = fa.analisar(s["c"], s["elab"], s["inv"], itens)
    assert [x["situacao"] for x in r] == ["pronto", "duplicado", "sem_unidade", "erro"]
    assert r[0]["unidade"] == "Hospital" and "2024" in r[3]["erro"]


def test_lancamento_no_escopo_2_com_comprovante_e_sem_duplicar(s):
    c = s["c"]
    aid = fa.lancar(c, s["elab"], s["inv"], uc="3012345678", ano=2025, mes=10, kwh=1234, anexo_id=s["anexo"])
    a = c.execute("SELECT * FROM atividade WHERE id=?", (aid,)).fetchone()
    assert (a["escopo"], a["mes"], a["instalacao_id"], a["anexo_id"]) == (2, 10, s["hosp"], s["anexo"])
    assert a["tco2e"] == pytest.approx(1234 * 0.0000385)
    with pytest.raises(ErroNegocio, match="já tem fatura"):
        fa.lancar(c, s["elab"], s["inv"], uc="3012345678", ano=2025, mes=10, kwh=1234, anexo_id=s["anexo"])
    with pytest.raises(ErroNegocio, match="inventário é de 2025"):
        fa.lancar(c, s["elab"], s["inv"], uc="3012345678", ano=2024, mes=10, kwh=1, anexo_id=s["anexo"])


def test_api_pdf_e_planilha_em_lote_tudo_ou_nada(tmp_path):
    app = criar_app(str(tmp_path / "api.db"))
    cli, c = TestClient(app), conectar(app.state.caminho)
    org = c.execute("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Pref','11111111000191','orgao_publico')").lastrowid
    elab = c.execute("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('E','elaborador',?)", (org,)).lastrowid
    H = {"X-Usuario-Id": str(elab)}
    assert cli.post(f"/organizacoes/{org}/instalacoes", headers=H, json={"nome": "Hospital", "uc_energia": "30123-45678"}).status_code == 201
    inv = cli.post("/inventarios", headers=H, json={"organizacao_id": org, "ano": 2025}).json()["id"]
    r = cli.post(f"/inventarios/{inv}/faturas/leitura", headers=H,
                 files={"arquivo": ("fatura-outubro.pdf", gerar_pdf(GRUPO_B), "application/pdf")}).json()
    assert r["leitura"]["kwh"] == 1234 and r["analise"]["unidade"] == "Hospital"
    assert c.execute("SELECT COUNT(*) FROM atividade").fetchone()[0] == 0  # leitura não lança
    ok = cli.post(f"/inventarios/{inv}/faturas", headers=H, json={**{k: r["leitura"][k] for k in ("uc", "ano", "mes", "kwh")},
                                                                  "anexo_id": r["anexo_id"]})
    assert ok.status_code == 201
    csv = "UC,Referência,kWh\n3012345678,11/2025,1000\n3012345678,10/2025,1234\n".encode()
    p = cli.post(f"/inventarios/{inv}/faturas/planilha", headers=H, files={"arquivo": ("contas.csv", csv, "application/vnd.ms-excel")}).json()
    assert [l["situacao"] for l in p["linhas"]] == ["pronto", "duplicado"]
    linhas = [{k: l[k] for k in ("uc", "ano", "mes", "kwh")} for l in p["linhas"]]
    assert cli.post(f"/inventarios/{inv}/faturas/lote", headers=H, json={"anexo_id": p["anexo_id"], "linhas": linhas}).status_code == 409
    assert c.execute("SELECT COUNT(*) FROM atividade").fetchone()[0] == 1  # nada da planilha entrou
    assert cli.post(f"/inventarios/{inv}/faturas/lote", headers=H, json={"anexo_id": p["anexo_id"], "linhas": linhas[:1]}).json() == {"lancadas": 1}
    assert cli.post(f"/inventarios/{inv}/faturas/leitura", headers=H,
                    files={"arquivo": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 409
