"""Antivírus (protocolo clamd) e bloqueio de conteúdo ativo nos anexos."""
import io
import os
import socket
import struct
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient

from plataforma import anexos, antivirus as av
from plataforma.api import criar_app
from plataforma.banco import ErroNegocio, conectar

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
PDF_LIMPO = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"


class ClamdFalso:
    """Servidor com o protocolo do clamd (zPING, zINSTREAM) para os testes não dependerem do ClamAV."""

    def __init__(self):
        self.assinaturas = {EICAR: "Eicar-Test-Signature"}
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.porta = self.sock.getsockname()[1]
        threading.Thread(target=self._servir, daemon=True).start()

    def _servir(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            with c:
                cmd = b""
                while not cmd.endswith(b"\0"):
                    cmd += c.recv(1)
                if cmd == b"zPING\0":
                    c.sendall(b"PONG\0")
                    continue
                dados = b""
                while True:
                    n = struct.unpack(">I", self._ler(c, 4))[0]
                    if n == 0:
                        break
                    dados += self._ler(c, n)
                achado = next((nome for sig, nome in self.assinaturas.items() if sig in dados), None)
                c.sendall((f"stream: {achado} FOUND" if achado else "stream: OK").encode() + b"\0")

    @staticmethod
    def _ler(c, n):
        b = b""
        while len(b) < n:
            b += c.recv(n - len(b))
        return b

    def fechar(self):
        self.sock.close()


@pytest.fixture
def clamd(monkeypatch):
    srv = ClamdFalso()
    monkeypatch.setenv("CARBONO_CLAMAV", f"127.0.0.1:{srv.porta}")
    monkeypatch.delenv("CARBONO_ANTIVIRUS", raising=False)
    yield srv
    srv.fechar()


@pytest.fixture
def s(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))
    c = conectar(str(tmp_path / "a.db"))
    org = c.execute("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Pref','11111111000191','orgao_publico')").lastrowid
    uid = c.execute("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('E','elaborador',?)", (org,)).lastrowid
    return {"c": c, "u": uid, "dir": tmp_path / "anexos"}


def xlsx(entradas: dict[str, bytes], metodo=zipfile.ZIP_DEFLATED) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", metodo) as z:
        for nome, dados in entradas.items():
            z.writestr(nome, dados)
    return b.getvalue()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------- cliente clamd
def test_cliente_clamd_limpo_infectado_e_indisponivel(clamd, monkeypatch):
    assert av.ping() and av.situacao() == "ok"
    assert av.examinar(PDF_LIMPO) is None
    assert av.examinar(b"a" * 200_000 + EICAR) == "Eicar-Test-Signature"  # vários blocos
    monkeypatch.setenv("CARBONO_CLAMAV", "127.0.0.1:1")
    assert av.situacao() == "indisponivel"
    with pytest.raises(av.AntivirusIndisponivel):
        av.examinar(PDF_LIMPO)


# ---------------------------------------------------------------- conteúdo ativo
@pytest.mark.parametrize("trecho", [b"/OpenAction << /S /JavaScript /JS (app.alert(1)) >>",
                                    b"/AA << /O << /S /J#61vaScript >> >>",  # nome escapado
                                    b"/A << /S /Launch /F (cmd.exe) >>",
                                    b"/Names << /EmbeddedFiles 3 0 R >>"])
def test_pdf_com_conteudo_ativo_e_recusado(trecho):
    with pytest.raises(av.ArquivoRecusado):
        av.verificar_conteudo("application/pdf", b"%PDF-1.4\n1 0 obj << " + trecho + b" >> endobj\n%%EOF\n")


def test_pdf_de_fatura_comum_passa():
    from tests.test_faturas import GRUPO_B, gerar_pdf
    av.verificar_conteudo("application/pdf", gerar_pdf(GRUPO_B))


def test_planilha_com_macro_ou_bomba_e_recusada():
    with pytest.raises(av.ArquivoRecusado, match="macro"):
        av.verificar_conteudo(XLSX, xlsx({"xl/vbaProject.bin": b"\0" * 10}))
    with pytest.raises(av.ArquivoRecusado, match="compress"):
        av.verificar_conteudo(XLSX, xlsx({"xl/sheet1.xml": b"0" * 5_000_000}))
    av.verificar_conteudo(XLSX, xlsx({"xl/sheet1.xml": b"<sheet>dados normais</sheet>"}))


def test_imagem_corrompida_ou_gigante_e_csv_binario():
    from PIL import Image
    with pytest.raises(av.ArquivoRecusado, match="corrompida"):
        av.verificar_conteudo("image/png", b"\x89PNG\r\n\x1a\nlixo")
    b = io.BytesIO()
    Image.new("1", (12000, 12000)).save(b, "PNG")
    with pytest.raises(av.ArquivoRecusado, match="dimensões"):
        av.verificar_conteudo("image/png", b.getvalue())
    with pytest.raises(av.ArquivoRecusado, match="binário"):
        av.verificar_conteudo("text/csv", b"uc;kwh\n1\x00;2\n")


# ---------------------------------------------------------------- integração com os anexos
def test_arquivo_infectado_nao_e_gravado_e_fica_na_auditoria(s, clamd):
    infectada = xlsx({"leia.txt": EICAR}, zipfile.ZIP_STORED)  # o clamd falso não descompacta
    with pytest.raises(ErroNegocio, match="antivírus"):
        anexos.salvar(s["c"], s["u"], "planilha.xlsx", XLSX, infectada)
    assert s["c"].execute("SELECT COUNT(*) FROM anexo").fetchone()[0] == 0
    assert not s["dir"].exists() or not any(s["dir"].iterdir())
    a = s["c"].execute("SELECT depois FROM auditoria WHERE acao='anexo_recusado'").fetchone()
    assert "virus:Eicar-Test-Signature" in a["depois"]
    assert anexos.salvar(s["c"], s["u"], "fatura.pdf", "application/pdf", PDF_LIMPO)["id"]


def test_antivirus_fora_do_ar_recusa_o_envio(s, monkeypatch):
    monkeypatch.setenv("CARBONO_CLAMAV", "127.0.0.1:1")
    monkeypatch.delenv("CARBONO_ANTIVIRUS", raising=False)
    with pytest.raises(av.AntivirusIndisponivel):
        anexos.salvar(s["c"], s["u"], "fatura.pdf", "application/pdf", PDF_LIMPO)
    monkeypatch.setenv("CARBONO_ANTIVIRUS", "desativado")  # decisão explícita: passa sem exame
    assert anexos.salvar(s["c"], s["u"], "fatura.pdf", "application/pdf", PDF_LIMPO)["id"]


def test_revarredura_bloqueia_arquivo_detectado_depois(s, clamd):
    aid = anexos.salvar(s["c"], s["u"], "nota.pdf", "application/pdf", PDF_LIMPO + b"% amostra-nova\n")["id"]
    assert anexos.varrer(s["c"]) == {"examinados": 1, "detectados": []}
    clamd.assinaturas[b"amostra-nova"] = "Malware.Novo-0"   # assinatura publicada depois
    r = anexos.varrer(s["c"])
    assert r["detectados"][0]["ameaca"] == "Malware.Novo-0"
    with pytest.raises(ErroNegocio, match="bloqueado"):
        anexos.abrir(s["c"], s["u"], aid)
    assert anexos.varrer(s["c"])["detectados"]  # idempotente
    assert s["c"].execute("SELECT COUNT(*) FROM auditoria WHERE acao='anexo_bloqueado'").fetchone()[0] == 1


def test_api_503_quando_antivirus_cai_e_saude_degradada(tmp_path, monkeypatch):
    monkeypatch.setenv("CARBONO_ANEXOS", str(tmp_path / "anexos"))
    monkeypatch.setenv("CARBONO_CLAMAV", "127.0.0.1:1")
    monkeypatch.delenv("CARBONO_ANTIVIRUS", raising=False)
    app = criar_app(str(tmp_path / "api.db"))
    cli, c = TestClient(app), conectar(app.state.caminho)
    uid = c.execute("INSERT INTO usuario (nome,papel) VALUES ('R','registrador')").lastrowid
    r = cli.post("/anexos", headers={"X-Usuario-Id": str(uid)}, files={"arquivo": ("f.pdf", PDF_LIMPO, "application/pdf")})
    assert r.status_code == 503 and "não foi aceito" in r.json()["erro"]
    assert cli.get("/saude").json()["antivirus"] == "indisponivel"


@pytest.mark.skipif(not os.environ.get("CARBONO_CLAMAV_REAL"), reason="defina CARBONO_CLAMAV_REAL=host:porta")
def test_clamav_real_detecta_eicar(monkeypatch):
    monkeypatch.setenv("CARBONO_CLAMAV", os.environ["CARBONO_CLAMAV_REAL"])
    assert av.examinar(EICAR) == "Eicar-Test-Signature"
    assert av.examinar(xlsx({"leia.txt": EICAR})) == "Eicar-Test-Signature"
    assert av.examinar(PDF_LIMPO) is None
