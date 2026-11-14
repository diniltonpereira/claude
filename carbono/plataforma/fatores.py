"""Fatores de emissão versionados e potenciais de aquecimento global (GWP).

Os fatores ficam no banco (tabela `fator_emissao`). Um valor publicado nunca é alterado
nem apagado: o registrador cria uma NOVA VERSÃO e a anterior continua disponível para
os inventários que a usaram (auditoria). Cada atividade guarda o id da versão exata.

Os valores iniciais são REFERÊNCIAS para o piloto. Antes de um inventário oficial,
atualize-os com a Ferramenta do Programa Brasileiro GHG Protocol (FGV) e com o fator
médio anual do SIN publicado pelo MCTI.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, NaoEncontrado

# GWP de 100 anos. AR5: padrão do Programa Brasileiro GHG Protocol até a adoção do AR6.
# A Lei 15.042/2024 remete aos relatórios do IPCC sem fixar a edição (art. 2º, XXXIII).
GWP_PADRAO: dict[str, dict[str, float]] = {
    "AR5": {"CO2": 1, "CH4": 28, "CH4-fossil": 28, "N2O": 265, "HFC-134a": 1300,
            "HFC-32": 677, "R-410A": 1924, "SF6": 23500},
    # AR6 (IPCC 2021, WG1, cap. 7): metano fóssil e não fóssil têm valores distintos.
    # CONFERIR os valores de HFC e SF6 na Tabela 7.SM.7 antes do uso oficial.
    "AR6": {"CO2": 1, "CH4": 27.0, "CH4-fossil": 29.8, "N2O": 273, "HFC-134a": 1530,
            "HFC-32": 771, "R-410A": 2256, "SF6": 25200},
}
FONTE_GWP = {"AR5": "IPCC AR5 (2014), GWP-100", "AR6": "IPCC AR6 (2021), GWP-100 — conferir HFC/SF6"}

FATORES_INICIAIS = [
    # código, descrição, unidade, escopo, valor fóssil puro, fração bio padrão, bio puro, fonte, ano
    ("diesel_b", "Óleo diesel B (fóssil + biodiesel)", "litro", 1, 0.002681, 0.14, 0.002431,
     "IPCC 2006, v.2, cap. 3 (diesel fóssil); densidade ANP", 2024),
    ("gasolina_c", "Gasolina C (gasolina A + etanol anidro)", "litro", 1, 0.002270, 0.30, 0.001526,
     "IPCC 2006, v.2, cap. 3 (gasolina A); densidade ANP", 2024),
    ("etanol_hidratado", "Etanol hidratado (100% biogênico)", "litro", 1, 0.0, 1.0, 0.001457,
     "IPCC 2006 — CO2 biogênico, não soma ao escopo 1", 2024),
    ("gnv", "Gás natural veicular / gás natural", "m3", 1, 0.001999, 0, 0, "IPCC 2006, v.2; PCI médio ANP", 2024),
    ("glp", "Gás liquefeito de petróleo (GLP)", "kg", 1, 0.002932, 0, 0, "IPCC 2006, v.2", 2024),
    ("energia_sin", "Energia elétrica do SIN (abordagem por localização)", "MWh", 2, 0.0385, 0, 0,
     "MCTI — fator médio anual do SIN (CONFERIR ano vigente)", 2023),
    ("energia_sin_kwh", "Energia elétrica do SIN, em kWh (fatura)", "kWh", 2, 0.0000385, 0, 0,
     "MCTI — fator médio anual do SIN (CONFERIR ano vigente)", 2023),
    ("residuo_aterro", "Resíduos sólidos enviados a aterro sanitário", "tonelada", 3, 0.86, 0, 0,
     "Estimativa simplificada IPCC Waste Model — substituir por cálculo FOD", 2024),
    ("viagem_aerea_nacional", "Viagem aérea doméstica por passageiro", "passageiro.km", 3, 0.000158, 0, 0,
     "Referência GHG Protocol — atualizar", 2024),
    ("deslocamento_carro", "Deslocamento em carro a gasolina C (média)", "km", 3, 0.000159, 0, 0,
     "Referência: 10 km/l, parcela fóssil da gasolina C — substituir por fator oficial", 2024),
]


@dataclass(frozen=True)
class Fator:
    id: int
    codigo: str
    versao: int
    descricao: str
    unidade: str
    escopo: int
    valor: float
    fracao_biogenica_padrao: float
    biogenico_por_unidade: float
    fonte: str
    ano_referencia: int
    vigente: bool


def _fator(row: sqlite3.Row) -> Fator:
    return Fator(row["id"], row["codigo"], row["versao"], row["descricao"], row["unidade"], row["escopo"],
                 row["valor"], row["fracao_biogenica_padrao"], row["biogenico_por_unidade"], row["fonte"],
                 row["ano_referencia"], bool(row["vigente"]))


def semear(con: sqlite3.Connection) -> None:
    """Carrega fatores e GWP iniciais num banco novo. Idempotente."""
    if con.execute("SELECT COUNT(*) FROM fator_emissao").fetchone()[0] == 0:
        con.executemany(
            """INSERT INTO fator_emissao (codigo, versao, descricao, unidade, escopo, valor,
               fracao_biogenica_padrao, biogenico_por_unidade, fonte, ano_referencia)
               VALUES (?,1,?,?,?,?,?,?,?,?)""", FATORES_INICIAIS)
    if con.execute("SELECT COUNT(*) FROM gwp").fetchone()[0] == 0:
        con.executemany("INSERT INTO gwp (conjunto, gas, valor, fonte) VALUES (?,?,?,?)",
                        [(c, g, v, FONTE_GWP[c]) for c, gases in GWP_PADRAO.items() for g, v in gases.items()])


def vigente(con: sqlite3.Connection, codigo: str) -> Fator:
    r = con.execute("SELECT * FROM fator_emissao WHERE codigo=? AND vigente=1", (codigo,)).fetchone()
    if r is None:
        raise ErroNegocio(f"Fator de emissão desconhecido: {codigo!r}")
    return _fator(r)


def por_id(con: sqlite3.Connection, fator_id: int) -> Fator:
    r = con.execute("SELECT * FROM fator_emissao WHERE id=?", (fator_id,)).fetchone()
    if r is None:
        raise NaoEncontrado(f"fator {fator_id} não encontrado")
    return _fator(r)


def listar(con: sqlite3.Connection, incluir_historico: bool = False) -> list[dict]:
    sql = "SELECT * FROM fator_emissao" + ("" if incluir_historico else " WHERE vigente=1")
    return [dict(r) for r in con.execute(sql + " ORDER BY codigo, versao")]


def gwp(con: sqlite3.Connection, conjunto: str, gas: str) -> float:
    r = con.execute("SELECT valor FROM gwp WHERE conjunto=? AND gas=?", (conjunto, gas)).fetchone()
    if r is None:
        raise ErroNegocio(f"Gás {gas!r} sem GWP no conjunto {conjunto}.")
    return r["valor"]


def conjuntos_gwp(con: sqlite3.Connection) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in con.execute("SELECT * FROM gwp ORDER BY conjunto, gas"):
        out.setdefault(r["conjunto"], {})[r["gas"]] = r["valor"]
    return out


def nova_versao(con: sqlite3.Connection, usuario_id: int, codigo: str, *, valor: float, fonte: str,
                ano_referencia: int, descricao: str | None = None, unidade: str | None = None,
                escopo: int | None = None, fracao_biogenica_padrao: float | None = None,
                biogenico_por_unidade: float | None = None) -> int:
    """Publica nova versão de um fator (ou um fator novo). A versão anterior deixa de ser vigente
    mas continua no banco, ligada aos inventários que a usaram."""
    u = con.execute("SELECT papel FROM usuario WHERE id=?", (usuario_id,)).fetchone()
    if u is None or u["papel"] != "registrador":
        raise NaoAutorizado("Só o registrador publica fatores de emissão.")
    if valor < 0:
        raise ErroNegocio("O fator não pode ser negativo.")
    atual = con.execute("SELECT * FROM fator_emissao WHERE codigo=? AND vigente=1", (codigo,)).fetchone()
    if atual is None and None in (descricao, unidade, escopo):
        raise ErroNegocio("Fator novo exige descrição, unidade e escopo.")
    base = dict(atual) if atual else {"versao": 0, "fracao_biogenica_padrao": 0, "biogenico_por_unidade": 0}
    novo = {
        "codigo": codigo, "versao": base["versao"] + 1,
        "descricao": descricao or base["descricao"], "unidade": unidade or base["unidade"],
        "escopo": escopo or base["escopo"], "valor": valor,
        "fracao_biogenica_padrao": base["fracao_biogenica_padrao"] if fracao_biogenica_padrao is None
        else fracao_biogenica_padrao,
        "biogenico_por_unidade": base["biogenico_por_unidade"] if biogenico_por_unidade is None
        else biogenico_por_unidade,
        "fonte": fonte, "ano_referencia": ano_referencia,
    }
    if atual:
        con.execute("UPDATE fator_emissao SET vigente=0 WHERE id=?", (atual["id"],))
    fid = con.execute(
        """INSERT INTO fator_emissao (codigo, versao, descricao, unidade, escopo, valor,
           fracao_biogenica_padrao, biogenico_por_unidade, fonte, ano_referencia, criado_por)
           VALUES (:codigo,:versao,:descricao,:unidade,:escopo,:valor,:fracao_biogenica_padrao,
           :biogenico_por_unidade,:fonte,:ano_referencia,:u)""", {**novo, "u": usuario_id}).lastrowid
    registrar(con, usuario_id, "nova_versao", "fator_emissao", fid,
              antes=dict(atual) if atual else None, depois=novo)
    return fid
