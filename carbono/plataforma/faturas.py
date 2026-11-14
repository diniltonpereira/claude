"""Importação de faturas de energia (CEMIG e outras distribuidoras) para o escopo 2.

Não há API pública da CEMIG para faturas. Este módulo oferece:
1. leitura do PDF da fatura: extrai a instalação (UC), o mês de referência e o consumo em kWh;
2. leitura de planilha (CSV/XLSX) com várias unidades (relação consolidada de contas);
3. lançamento no inventário com o arquivo como comprovante, sem duplicar a mesma UC no mesmo mês.

A leitura NUNCA lança sozinha: devolve os valores com o trecho de origem e o grau de confiança, e a
pessoa confirma ou corrige. O layout das faturas muda; ajuste os padrões em `PADROES` se necessário.

Se a distribuidora oferecer acesso por API (canal de grandes clientes), implemente `FonteFaturas`
devolvendo `LeituraFatura` e reutilize `lancar` sem mudar o resto.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .banco import ErroNegocio, NaoAutorizado, buscar
from .mrv import EDITAVEL, adicionar_atividade

MESES = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10,
         "NOV": 11, "DEZ": 12}
MAX_PAGINAS = 20
FATOR_KWH = "energia_sin_kwh"


@dataclass
class LeituraFatura:
    uc: str | None = None
    ano: int | None = None
    mes: int | None = None
    kwh: float | None = None
    confianca: str = "baixa"            # alta | media | baixa
    avisos: list[str] = field(default_factory=list)
    trechos: dict[str, str] = field(default_factory=dict)

    def como_dict(self) -> dict:
        return asdict(self)


class FonteFaturas(Protocol):
    """Conector futuro (API da distribuidora). Deve devolver leituras já estruturadas."""
    def faturas(self, uc: str, ano: int) -> list[LeituraFatura]: ...


# ---------------------------------------------------------------- utilidades
def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").upper()


def numero_br(t: str) -> float:
    """'1.234,56' -> 1234.56; '1234' -> 1234.0; '1.234' (milhar) -> 1234.0."""
    t = t.strip()
    if "," in t:
        return float(t.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        return float(t.replace(".", ""))
    return float(t)


def _mes_ano(t: str) -> tuple[int, int] | None:
    t = _sem_acento(t).strip()
    m = re.search(r"\b([A-Z]{3})[A-Z]*\s*(?:/|DE|-)\s*(\d{4})\b", t)
    if m and m.group(1) in MESES:
        return int(m.group(2)), MESES[m.group(1)]
    m = re.search(r"\b(0?[1-9]|1[0-2])\s*/\s*(\d{4})\b", t)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = re.search(r"\b(\d{4})-(0[1-9]|1[0-2])\b", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


# ---------------------------------------------------------------- PDF
def extrair_texto_pdf(conteudo: bytes) -> str:
    from pypdf import PdfReader
    try:
        leitor = PdfReader(io.BytesIO(conteudo))
        if len(leitor.pages) > MAX_PAGINAS:
            raise ErroNegocio(f"PDF com mais de {MAX_PAGINAS} páginas: envie a fatura individual.")
        return "\n".join((p.extract_text() or "") for p in leitor.pages)
    except ErroNegocio:
        raise
    except Exception as exc:  # PDF corrompido, protegido ou escaneado sem texto
        raise ErroNegocio("Não foi possível ler o texto do PDF. Se for imagem escaneada, digite os valores.") from exc


NUM = r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)"
PADROES = {
    "uc": [r"(?:N[º°O.]*\s*DA\s*)?INSTALA[CÇ][AÃ]O[\s:Nº°.]*?(\d{6,12})",
           r"UNIDADE\s+CONSUMIDORA[\s:Nº°.]*?(\d{6,12})"],
    "referencia": [r"REFER[EÊ]NTE\s+A[:\s]*([A-Z]{3,9}\s*(?:/|DE)\s*\d{4}|\d{1,2}\s*/\s*\d{4})",
                   r"M[EÊ]S\s+(?:DE\s+)?REFER[EÊ]NCIA[:\s]*([A-Z]{3,9}\s*(?:/|DE)\s*\d{4}|\d{1,2}\s*/\s*\d{4})",
                   r"COMPET[EÊ]NCIA[:\s]*([A-Z]{3,9}\s*(?:/|DE)\s*\d{4}|\d{1,2}\s*/\s*\d{4})"],
    "consumo_total": [r"CONSUMO\s+(?!MEDIO|DIARIO)(?:TOTAL\s+)?(?:FATURADO\s+|REGISTRADO\s+|APURADO\s+)?(?:EM\s+)?KWH[\s:]*" + NUM,
                      r"CONSUMO\s+(?!MEDIO|DIARIO)(?:TOTAL\s+)?(?:FATURADO\s+|REGISTRADO\s+|APURADO\s+)?[:\s]*" + NUM + r"\s*KWH"],
    "ponta": [r"CONSUMO\s+(?:ATIVO\s+)?(?:NA\s+)?PONTA[^\n]{0,20}?" + NUM + r"\s*KWH",
              r"CONSUMO\s+(?:ATIVO\s+)?(?:NA\s+)?PONTA\s+KWH[\s:]*" + NUM],
    "fora_ponta": [r"CONSUMO\s+(?:ATIVO\s+)?FORA\s+(?:DE\s+)?PONTA[^\n]{0,20}?" + NUM + r"\s*KWH",
                   r"CONSUMO\s+(?:ATIVO\s+)?FORA\s+(?:DE\s+)?PONTA\s+KWH[\s:]*" + NUM],
    "energia_linha": [r"ENERGIA\s+EL[EÉ]TRICA[^\n]{0,15}?KWH\s+" + NUM],
}


def _primeiro(padroes: list[str], texto: str) -> re.Match | None:
    for p in padroes:
        m = re.search(p, texto)
        if m:
            return m
    return None


def ler_fatura(texto: str) -> LeituraFatura:
    """Extrai UC, referência e consumo. Sempre exige conferência humana."""
    t = _sem_acento(texto)
    r = LeituraFatura()
    m = _primeiro(PADROES["uc"], t)
    if m:
        r.uc, r.trechos["uc"] = m.group(1), m.group(0).strip()
    m = _primeiro(PADROES["referencia"], t)
    if m and (ma := _mes_ano(m.group(1))):
        r.ano, r.mes = ma
        r.trechos["referencia"] = m.group(0).strip()
    total = _primeiro(PADROES["consumo_total"], t)
    ponta, fora = _primeiro(PADROES["ponta"], t), _primeiro(PADROES["fora_ponta"], t)
    metodo = None
    if ponta and fora:
        r.kwh = numero_br(ponta.group(1)) + numero_br(fora.group(1))
        r.trechos["consumo"] = f"{ponta.group(0).strip()} + {fora.group(0).strip()}"
        metodo = "ponta_fora"
        r.avisos.append("Fatura do grupo A: somados os consumos na ponta e fora de ponta. Confira se há outros postos.")
    elif total:
        r.kwh, r.trechos["consumo"], metodo = numero_br(total.group(1)), total.group(0).strip(), "total"
    else:
        linhas = re.findall(PADROES["energia_linha"][0], t)
        if linhas:
            # TE e TUSD costumam repetir a mesma quantidade: usa a maior, não a soma
            valores = [numero_br(v) for v in linhas]
            r.kwh, metodo = max(valores), "linha"
            r.trechos["consumo"] = f"linhas de energia elétrica: {', '.join(linhas)} kWh (usada a maior)"
            r.avisos.append("Consumo inferido das linhas de energia (TE/TUSD). Confira o total na fatura.")
    if "INJETADA" in t or "COMPENSAD" in t or "GERACAO DISTRIBUIDA" in t:
        r.avisos.append("Unidade com geração distribuída: lance o consumo REGISTRADO (energia retirada da rede), "
                        "não o faturado após a compensação. A geração própria pode ser lançada como emissão evitada.")
    achados = sum(x is not None for x in (r.uc, r.mes, r.kwh))
    r.confianca = "alta" if achados == 3 and metodo == "total" else "media" if achados == 3 else "baixa"
    if achados < 3:
        faltam = [n for n, v in (("instalação", r.uc), ("mês de referência", r.mes), ("consumo em kWh", r.kwh)) if v is None]
        r.avisos.append("Não encontrado no PDF: " + ", ".join(faltam) + ". Preencha a partir da fatura.")
    return r


# ---------------------------------------------------------------- planilha
CABECALHOS = {
    "uc": ("INSTALACAO", "UC", "UNIDADE CONSUMIDORA", "N INSTALACAO", "NUMERO DA INSTALACAO", "CODIGO"),
    "referencia": ("REFERENCIA", "MES", "MES REFERENCIA", "COMPETENCIA", "MES/ANO", "PERIODO"),
    "kwh": ("KWH", "CONSUMO", "CONSUMO KWH", "CONSUMO (KWH)", "CONSUMO FATURADO", "CONSUMO REGISTRADO"),
}


def _chave_cabecalho(h: str) -> str | None:
    n = re.sub(r"[^A-Z0-9/ ()]", "", _sem_acento(str(h or ""))).replace("Nº", "N").strip()
    n = re.sub(r"\s+", " ", n)
    for campo, nomes in CABECALHOS.items():
        if n in nomes:
            return campo
    return None


def ler_planilha(nome: str, conteudo: bytes) -> list[dict]:
    if nome.lower().endswith(".xlsx"):
        from openpyxl import load_workbook
        try:
            ws = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True).active
            linhas = [list(r) for r in ws.iter_rows(values_only=True)]
        except Exception as exc:
            raise ErroNegocio("Não foi possível ler a planilha XLSX.") from exc
    else:
        texto = conteudo.decode("utf-8-sig", errors="replace")
        dialeto = ";" if texto.count(";") > texto.count(",") else ","
        linhas = list(csv.reader(io.StringIO(texto), delimiter=dialeto))
    if not linhas:
        raise ErroNegocio("Planilha vazia.")
    mapa = {i: _chave_cabecalho(h) for i, h in enumerate(linhas[0])}
    if not {"uc", "referencia", "kwh"} <= set(mapa.values()):
        raise ErroNegocio("A planilha precisa das colunas: Instalação (ou UC), Referência (mês/ano) e Consumo (kWh).")
    saida = []
    for n, linha in enumerate(linhas[1:], start=2):
        if not any(c not in (None, "") for c in linha):
            continue
        d = {campo: linha[i] if i < len(linha) else None for i, campo in mapa.items() if campo}
        item = {"linha": n, "uc": None, "ano": None, "mes": None, "kwh": None, "erro": None}
        uc = "".join(ch for ch in str(d["uc"] or "") if ch.isdigit())
        ref = d["referencia"]
        ma = (ref.year, ref.month) if hasattr(ref, "year") else _mes_ano(str(ref or ""))
        try:
            kwh = float(d["kwh"]) if isinstance(d["kwh"], (int, float)) else numero_br(str(d["kwh"]))
        except (TypeError, ValueError):
            kwh = None
        if not uc:
            item["erro"] = "instalação vazia"
        elif not ma:
            item["erro"] = f"referência inválida: {ref!r}"
        elif kwh is None or kwh < 0:
            item["erro"] = f"consumo inválido: {d['kwh']!r}"
        item.update(uc=uc or None, kwh=kwh)
        if ma:
            item["ano"], item["mes"] = ma
        saida.append(item)
    return saida


# ---------------------------------------------------------------- lançamento no inventário
def _chave(inventario_id: int, uc: str, ano: int, mes: int) -> str:
    return f"fatura:{inventario_id}:{uc}:{ano}-{mes:02d}"


def analisar(con: sqlite3.Connection, usuario_id: int, inventario_id: int, itens: list[dict]) -> list[dict]:
    """Classifica cada item: pronto, duplicado, fora do ano, sem unidade vinculada ou com erro."""
    inv = buscar(con, "inventario", inventario_id)
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "elaborador" or u["organizacao_id"] != inv["organizacao_id"]:
        raise NaoAutorizado("Só um elaborador da organização importa faturas.")
    unidades = {r["uc_energia"]: (r["id"], r["nome"]) for r in con.execute(
        "SELECT id, nome, uc_energia FROM instalacao WHERE organizacao_id=? AND uc_energia IS NOT NULL",
        (inv["organizacao_id"],))}
    vistos = set()
    out = []
    for it in itens:
        it = dict(it)
        it["situacao"], it["unidade"], it["instalacao_id"] = "pronto", None, None
        if it.get("erro"):
            it["situacao"] = "erro"
        elif it["ano"] != inv["ano"]:
            it["situacao"], it["erro"] = "erro", f"referência de {it['ano']}, inventário de {inv['ano']}"
        else:
            chave = _chave(inventario_id, it["uc"], it["ano"], it["mes"])
            if chave in vistos or con.execute("SELECT 1 FROM atividade WHERE chave_idempotencia=?", (chave,)).fetchone():
                it["situacao"], it["erro"] = "duplicado", "esta UC já tem fatura lançada neste mês"
            vistos.add(chave)
            if it["uc"] in unidades:
                it["instalacao_id"], it["unidade"] = unidades[it["uc"]]
            elif it["situacao"] == "pronto":
                it["situacao"] = "sem_unidade"  # lança sem vínculo, com aviso
        out.append(it)
    return out


def lancar(con: sqlite3.Connection, usuario_id: int, inventario_id: int, *, uc: str, ano: int, mes: int,
           kwh: float, anexo_id: int, instalacao_id: int | None = None, origem: str = "fatura") -> int:
    inv = buscar(con, "inventario", inventario_id)
    if inv["status"] not in EDITAVEL:
        raise ErroNegocio(f"Inventário em '{inv['status']}' não aceita lançamentos.")
    if ano != inv["ano"]:
        raise ErroNegocio(f"A fatura é de {ano}; o inventário é de {inv['ano']}.")
    if not 1 <= mes <= 12 or kwh < 0:
        raise ErroNegocio("Mês ou consumo inválido.")
    uc = "".join(ch for ch in str(uc) if ch.isdigit())
    if not uc:
        raise ErroNegocio("Informe o número da instalação (UC).")
    chave = _chave(inventario_id, uc, ano, mes)
    if con.execute("SELECT 1 FROM atividade WHERE chave_idempotencia=?", (chave,)).fetchone():
        raise ErroNegocio(f"A UC {uc} já tem fatura lançada em {mes:02d}/{ano}.")
    if instalacao_id is None:
        r = con.execute("SELECT id FROM instalacao WHERE organizacao_id=? AND uc_energia=?",
                        (inv["organizacao_id"], uc)).fetchone()
        instalacao_id = r["id"] if r else None
    return adicionar_atividade(
        con, usuario_id, inventario_id, descricao=f"Energia elétrica — UC {uc} ({mes:02d}/{ano})",
        fator=FATOR_KWH, quantidade=kwh, mes=mes, instalacao_id=instalacao_id, anexo_id=anexo_id,
        evidencia=f"{'Fatura' if origem == 'fatura' else 'Planilha de faturas'} da distribuidora, UC {uc}, ref. {mes:02d}/{ano}",
        chave_idempotencia=chave)
