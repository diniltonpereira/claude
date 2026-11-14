"""Proteção dos anexos: antivírus (ClamAV) e bloqueio de conteúdo ativo.

1. ClamAV: cada arquivo é examinado pelo `clamd` (protocolo INSTREAM) ANTES de ser gravado.
   Configure CARBONO_CLAMAV com "host:porta" (ex.: clamav:3310) ou o caminho do socket Unix.
   Em produção o antivírus é obrigatório: sem ele, a aplicação não sobe e, se ele cair, os envios
   são recusados (falha fechada). Só CARBONO_ANTIVIRUS=desativado dispensa, por decisão explícita.
2. Conteúdo ativo, recusado mesmo que o antivírus não acuse (faturas e certidões não precisam dele):
   - PDF com JavaScript, ação de abrir programa, arquivo embutido, mídia ou formulário XFA;
   - planilha com macro ou ActiveX; "bomba" de compressão;
   - imagem corrompida ou com dimensões absurdas (bomba de descompressão).
3. Revarredura: `python -m plataforma varrer-anexos` reexamina os arquivos guardados (as assinaturas
   mudam todo dia); o que for detectado fica bloqueado para download.
"""
from __future__ import annotations

import io
import os
import re
import socket
import struct
import zipfile

from .banco import ErroNegocio

BLOCO = 64 * 1024
TEMPO_LIMITE = float(os.environ.get("CARBONO_CLAMAV_TEMPO", "30"))


class AntivirusIndisponivel(Exception):
    """O antivírus não respondeu (HTTP 503): o envio é recusado em vez de passar sem exame."""


class ArquivoRecusado(ErroNegocio):
    def __init__(self, mensagem: str, motivo: str):
        super().__init__(mensagem)
        self.motivo = motivo


# ---------------------------------------------------------------- ClamAV (clamd)
def alvo() -> str | None:
    return os.environ.get("CARBONO_CLAMAV") or None


def obrigatorio() -> bool:
    return os.environ.get("CARBONO_ANTIVIRUS", "obrigatorio") != "desativado"


def _conectar() -> socket.socket:
    a = alvo()
    if not a:
        raise AntivirusIndisponivel("Antivírus não configurado (CARBONO_CLAMAV).")
    try:
        if a.startswith("/"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(TEMPO_LIMITE)
            s.connect(a)
        else:
            host, porta = a.rsplit(":", 1)
            s = socket.create_connection((host, int(porta)), timeout=TEMPO_LIMITE)
        return s
    except OSError as exc:
        raise AntivirusIndisponivel(f"Antivírus inacessível em {a}.") from exc


def _resposta(s: socket.socket) -> str:
    dados = b""
    while not dados.endswith(b"\0"):
        parte = s.recv(4096)
        if not parte:
            break
        dados += parte
    return dados.rstrip(b"\0").decode(errors="replace").strip()


def ping() -> bool:
    try:
        with _conectar() as s:
            s.sendall(b"zPING\0")
            return _resposta(s) == "PONG"
    except (AntivirusIndisponivel, OSError):
        return False


def examinar(conteudo: bytes) -> str | None:
    """Devolve o nome da ameaça encontrada ou None se o arquivo estiver limpo."""
    try:
        with _conectar() as s:
            s.sendall(b"zINSTREAM\0")
            for i in range(0, len(conteudo), BLOCO):
                bloco = conteudo[i:i + BLOCO]
                s.sendall(struct.pack(">I", len(bloco)) + bloco)
            s.sendall(struct.pack(">I", 0))
            r = _resposta(s)
    except OSError as exc:
        raise AntivirusIndisponivel("O antivírus não respondeu.") from exc
    if r.endswith("OK"):
        return None
    m = re.match(r"stream:\s*(.+?)\s+FOUND$", r)
    if m:
        return m.group(1)
    raise AntivirusIndisponivel(f"Resposta inesperada do antivírus: {r[:120]}")


def situacao() -> str:
    if not alvo():
        return "nao_configurado"
    return "ok" if ping() else "indisponivel"


# ---------------------------------------------------------------- conteúdo ativo
_NOME_ESCAPADO = re.compile(rb"#([0-9A-Fa-f]{2})")
_PDF_PROIBIDO = {
    rb"/JavaScript": "JavaScript", rb"/JS": "JavaScript", rb"/Launch": "ação que abre programas",
    rb"/EmbeddedFile": "arquivo embutido", rb"/EmbeddedFiles": "arquivo embutido", rb"/RichMedia": "mídia embutida", rb"/XFA": "formulário XFA",
    rb"/SubmitForm": "envio de formulário", rb"/ImportData": "importação de dados",
}


def _pdf(conteudo: bytes) -> None:
    # Nomes PDF podem vir escapados (/J#61vaScript): normaliza antes de procurar.
    bruto = _NOME_ESCAPADO.sub(lambda m: bytes([int(m.group(1), 16)]), conteudo)
    for chave, nome in _PDF_PROIBIDO.items():
        if re.search(re.escape(chave) + rb"(?![A-Za-z])", bruto):
            raise ArquivoRecusado(f"PDF com conteúdo ativo ({nome}) não é aceito. Envie a fatura ou o documento "
                                  "sem scripts nem anexos embutidos (por exemplo, \"imprimir como PDF\").", nome)
    # Também nos fluxos comprimidos e no catálogo, pela leitura estruturada.
    try:
        from pypdf import PdfReader
        leitor = PdfReader(io.BytesIO(conteudo))
        raiz = leitor.trailer["/Root"]
        nomes = raiz.get("/Names") or {}
        if "/JavaScript" in nomes or "/EmbeddedFiles" in nomes or "/AA" in raiz:
            raise ArquivoRecusado("PDF com ações automáticas ou anexos embutidos não é aceito.", "catalogo")
        acao = raiz.get("/OpenAction")
        if hasattr(acao, "get") and acao.get("/S") in ("/JavaScript", "/Launch"):
            raise ArquivoRecusado("PDF com ação automática ao abrir não é aceito.", "openaction")
        for pagina in leitor.pages[:50]:
            if "/AA" in pagina:
                raise ArquivoRecusado("PDF com ações automáticas não é aceito.", "pagina_aa")
            for anot in pagina.get("/Annots") or []:
                a = anot.get_object().get("/A") or {}
                if hasattr(a, "get") and a.get("/S") in ("/JavaScript", "/Launch", "/SubmitForm", "/ImportData"):
                    raise ArquivoRecusado("PDF com ação ativa em anotação não é aceito.", "anotacao")
    except ArquivoRecusado:
        raise
    except Exception:  # PDF malformado: o leitor de faturas avisará; o antivírus já examinou
        pass


def _xlsx(conteudo: bytes) -> None:
    try:
        z = zipfile.ZipFile(io.BytesIO(conteudo))
        infos = z.infolist()
    except zipfile.BadZipFile as exc:
        raise ArquivoRecusado("Planilha XLSX inválida.", "zip_invalido") from exc
    if len(infos) > 2000:
        raise ArquivoRecusado("Planilha com estrutura anormal (arquivos internos demais).", "zip_entradas")
    total = sum(i.file_size for i in infos)
    if total > 200 * 1024 * 1024:
        raise ArquivoRecusado("Planilha que se expande demais ao abrir (possível bomba de compressão).", "zip_bomba")
    for i in infos:
        nome = i.filename.lower()
        if nome.endswith("vbaproject.bin") or "activex" in nome or nome.endswith(".bin"):
            raise ArquivoRecusado("Planilha com macro ou controle ActiveX não é aceita. Salve como .xlsx sem macros.",
                                  "macro")
        if i.compress_size and i.file_size / i.compress_size > 500:
            raise ArquivoRecusado("Planilha com compressão anormal (possível bomba de compressão).", "zip_razao")


def _imagem(conteudo: bytes) -> None:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 60_000_000
    try:
        with Image.open(io.BytesIO(conteudo)) as im:
            im.verify()
    except Image.DecompressionBombError as exc:
        raise ArquivoRecusado("Imagem com dimensões exageradas não é aceita.", "imagem_bomba") from exc
    except Exception as exc:
        raise ArquivoRecusado("Imagem corrompida ou em formato inválido.", "imagem_invalida") from exc


def _csv(conteudo: bytes) -> None:
    if b"\0" in conteudo:
        raise ArquivoRecusado("CSV com conteúdo binário não é aceito.", "csv_binario")


VERIFICADORES = {
    "application/pdf": _pdf,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _xlsx,
    "image/jpeg": _imagem, "image/png": _imagem,
    "text/csv": _csv,
}


def verificar_conteudo(tipo_mime: str, conteudo: bytes) -> None:
    f = VERIFICADORES.get(tipo_mime)
    if f:
        f(conteudo)
