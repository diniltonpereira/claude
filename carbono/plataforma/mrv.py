"""MRV — Mensuração (aferição), Relato e Verificação, com moderação final.

Fluxo:
    rascunho ─submeter─► submetido ─assumir─► em_verificacao ─┬─verificar─► verificado ─aprovar─► aprovado
        ▲                                                      └─devolver──► devolvido
        └──────────────── (correção) ◄── devolvido ◄─devolver── verificado

Segregação de funções (controle interno, Lei 14.133/2021, art. 7º, §1º):
- quem elabora não verifica nem modera;
- o verificador precisa ser organismo de inspeção acreditado (Lei 15.042/2024, art. 32,
  parágrafo único) e não pode pertencer à organização inventariada (independência, art. 44, II);
- o moderador não pode ter elaborado nem verificado o mesmo inventário.

Comprovação: cada lançamento exige anexo (NF, fatura, laudo, foto). Sem anexo, fica
"pendente de comprovação" e NÃO entra nos totais nem no relatório. Emissões evitadas (solar,
biogás, reciclagem) são relatadas à parte e não abatem o inventário. Toda inclusão, edição e
exclusão vai para a trilha de auditoria.
"""
from __future__ import annotations

import sqlite3

from . import fatores
from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, buscar, transacao
from .calculo import calcular_por_fator, calcular_por_gas

# (status atual, ação) -> (novo status, papel exigido)
TRANSICOES: dict[tuple[str, str], tuple[str, str]] = {
    ("rascunho", "submeter"): ("submetido", "elaborador"),
    ("devolvido", "submeter"): ("submetido", "elaborador"),
    ("submetido", "assumir"): ("em_verificacao", "verificador"),
    ("em_verificacao", "verificar"): ("verificado", "verificador"),
    ("em_verificacao", "devolver"): ("devolvido", "verificador"),
    ("verificado", "aprovar"): ("aprovado", "moderador"),
    ("verificado", "devolver"): ("devolvido", "moderador"),
}
EDITAVEL = ("rascunho", "devolvido")
# Atividade comprovada: tem anexo, ou veio de contrato (a prova está nos lançamentos da terceirizada).
COMPROVADA = "(a.anexo_id IS NOT NULL OR a.contrato_id IS NOT NULL)"


# ---------------------------------------------------------------- inventário e unidades
def criar_inventario(con: sqlite3.Connection, usuario_id: int, organizacao_id: int, ano: int,
                     gwp_conjunto: str = "AR5") -> int:
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "elaborador" or u["organizacao_id"] != organizacao_id:
        raise NaoAutorizado("Só um elaborador da própria organização cria o inventário.")
    if gwp_conjunto not in fatores.conjuntos_gwp(con):
        raise ErroNegocio(f"Conjunto de GWP desconhecido: {gwp_conjunto!r}.")
    try:
        iid = con.execute(
            "INSERT INTO inventario (organizacao_id, ano, gwp_conjunto, elaborado_por) VALUES (?,?,?,?)",
            (organizacao_id, ano, gwp_conjunto, usuario_id)).lastrowid
    except sqlite3.IntegrityError as exc:
        raise ErroNegocio(f"Já existe inventário de {ano} para esta organização.") from exc
    registrar(con, usuario_id, "criar", "inventario", iid, depois={"ano": ano, "gwp": gwp_conjunto})
    return iid


def criar_instalacao(con: sqlite3.Connection, usuario_id: int, organizacao_id: int, *, nome: str,
                     natureza: str = "instalacao", pai_id: int | None = None,
                     identificador: str | None = None, municipio_uf: str | None = None,
                     agropecuaria_primaria: bool = False, residuos_neutralizacao: bool = False) -> int:
    """Cadastra unidade (secretaria, hospital…), instalação (art. 2º, XV) ou fonte móvel (art. 2º, XII).
    `pai_id` monta a hierarquia órgão > unidade > prédio/instalação."""
    u = buscar(con, "usuario", usuario_id)
    buscar(con, "organizacao", organizacao_id)
    if not (u["papel"] == "registrador"
            or (u["papel"] == "elaborador" and u["organizacao_id"] == organizacao_id)):
        raise NaoAutorizado("Só o registrador ou um elaborador da organização cadastra unidades.")
    if pai_id is not None:
        pai = buscar(con, "instalacao", pai_id)
        if pai["organizacao_id"] != organizacao_id:
            raise ErroNegocio("A unidade superior pertence a outra organização.")
        if pai["natureza"] == "fonte_movel":
            raise ErroNegocio("Fonte móvel não pode ter unidades subordinadas.")
    try:
        iid = con.execute(
            """INSERT INTO instalacao (organizacao_id, pai_id, nome, natureza, identificador, municipio_uf,
               agropecuaria_primaria, residuos_neutralizacao) VALUES (?,?,?,?,?,?,?,?)""",
            (organizacao_id, pai_id, nome, natureza, identificador, municipio_uf,
             int(agropecuaria_primaria), int(residuos_neutralizacao))).lastrowid
    except sqlite3.IntegrityError as exc:
        raise ErroNegocio(f"Unidade inválida ou com nome repetido: {exc}") from exc
    registrar(con, usuario_id, "criar", "instalacao", iid, depois={"nome": nome, "natureza": natureza,
                                                                  "pai_id": pai_id})
    return iid


def listar_instalacoes(con: sqlite3.Connection, organizacao_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM instalacao WHERE organizacao_id=? ORDER BY id", (organizacao_id,))]


# ---------------------------------------------------------------- atividades
def _exigir_edicao(con, usuario_id: int, inv: sqlite3.Row) -> None:
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "elaborador" or u["organizacao_id"] != inv["organizacao_id"]:
        raise NaoAutorizado("Só um elaborador da organização altera atividades.")
    if inv["status"] not in EDITAVEL:
        raise ErroNegocio(f"Inventário em '{inv['status']}' não aceita alterações.")


def _validar_anexo(con, usuario_id: int, anexo_id: int | None) -> None:
    if anexo_id is not None:
        from .anexos import exigir_acesso
        exigir_acesso(con, usuario_id, anexo_id)


def _calcular(con, inv, *, fator_id=None, gas=None, quantidade, escopo=1, fracao_biogenica=None):
    try:
        if fator_id is not None:
            return calcular_por_fator(fatores.por_id(con, fator_id), quantidade, fracao_biogenica)
        return calcular_por_gas(gas, quantidade, fatores.gwp(con, inv["gwp_conjunto"], gas),
                                inv["gwp_conjunto"], escopo)
    except ValueError as exc:
        raise ErroNegocio(str(exc)) from exc


def adicionar_atividade(con: sqlite3.Connection, usuario_id: int, inventario_id: int, *,
                        descricao: str, quantidade: float, fator: str | None = None,
                        gas: str | None = None, escopo: int = 1,
                        fracao_biogenica: float | None = None, evidencia: str | None = None,
                        remocao: bool = False, evitada: bool = False,
                        instalacao_id: int | None = None, mes: int | None = None,
                        anexo_id: int | None = None) -> int:
    """Lança uma atividade.

    - `fator`: código do fator; grava-se a versão vigente (fica fixada na atividade).
    - `gas`: massa do gás em toneladas, convertida pelo GWP do conjunto do inventário.
    - `remocao=True`: remoção por sumidouro (só CO2), reduz emissões líquidas (art. 2º, XI).
    - `evitada=True`: emissão evitada (solar, biogás, reciclagem), relatada à parte.
    - sem `anexo_id`: fica pendente de comprovação e fora dos totais.
    """
    inv = buscar(con, "inventario", inventario_id)
    _exigir_edicao(con, usuario_id, inv)
    if (fator is None) == (gas is None):
        raise ErroNegocio("Informe exatamente um: 'fator' ou 'gas'.")
    if remocao and evitada:
        raise ErroNegocio("Uma atividade não pode ser remoção e emissão evitada ao mesmo tempo.")
    if remocao and gas != "CO2":
        raise ErroNegocio("Remoções são lançadas como massa de CO2 removida (gas='CO2').")
    if instalacao_id is not None and buscar(con, "instalacao", instalacao_id)["organizacao_id"] \
            != inv["organizacao_id"]:
        raise ErroNegocio("Instalação de outra organização.")
    _validar_anexo(con, usuario_id, anexo_id)
    fator_id = fatores.vigente(con, fator).id if fator else None
    r = _calcular(con, inv, fator_id=fator_id, gas=gas, quantidade=quantidade, escopo=escopo,
                  fracao_biogenica=fracao_biogenica)
    natureza = "remocao" if remocao else "evitada" if evitada else "emissao"
    prefixo = {"remocao": "REMOÇÃO: ", "evitada": "EMISSÃO EVITADA (não abate): ", "emissao": ""}[natureza]
    dados = dict(inventario_id=inventario_id, instalacao_id=instalacao_id, mes=mes, descricao=descricao,
                 escopo=r.escopo, natureza=natureza, origem=f"fator:{fator}" if fator else f"gas:{gas}",
                 fator_id=fator_id, quantidade=quantidade, tco2e=r.tco2e, tco2_biogenico=r.tco2_biogenico,
                 memoria=prefixo + r.memoria, evidencia=evidencia, anexo_id=anexo_id)
    try:
        aid = con.execute(
            f"INSERT INTO atividade ({', '.join(dados)}) VALUES ({', '.join('?' * len(dados))})",
            tuple(dados.values())).lastrowid
    except sqlite3.IntegrityError as exc:
        raise ErroNegocio(f"Atividade inválida: {exc}") from exc
    registrar(con, usuario_id, "criar", "atividade", aid, depois=dados)
    return aid


def editar_atividade(con: sqlite3.Connection, usuario_id: int, atividade_id: int, *,
                     descricao: str | None = None, quantidade: float | None = None,
                     mes: int | None = None, instalacao_id: int | None = None,
                     anexo_id: int | None = None, evidencia: str | None = None) -> None:
    """Edita uma atividade de inventário editável. A versão do fator usada originalmente é mantida;
    o valor anterior fica na trilha de auditoria."""
    antes = buscar(con, "atividade", atividade_id)
    if antes["contrato_id"] is not None:
        raise ErroNegocio("Atividade importada de contrato: corrija os lançamentos da terceirizada.")
    inv = buscar(con, "inventario", antes["inventario_id"])
    _exigir_edicao(con, usuario_id, inv)
    _validar_anexo(con, usuario_id, anexo_id)
    if instalacao_id is not None and buscar(con, "instalacao", instalacao_id)["organizacao_id"] \
            != inv["organizacao_id"]:
        raise ErroNegocio("Instalação de outra organização.")
    novo = dict(antes)
    for campo, valor in (("descricao", descricao), ("mes", mes), ("instalacao_id", instalacao_id),
                         ("anexo_id", anexo_id), ("evidencia", evidencia)):
        if valor is not None:
            novo[campo] = valor
    if quantidade is not None:
        gas = antes["origem"][4:] if antes["origem"].startswith("gas:") else None
        r = _calcular(con, inv, fator_id=antes["fator_id"], gas=gas, quantidade=quantidade,
                      escopo=antes["escopo"])
        prefixo = antes["memoria"].split(": ")[0] + ": " if antes["natureza"] != "emissao" else ""
        novo.update(quantidade=quantidade, tco2e=r.tco2e, tco2_biogenico=r.tco2_biogenico,
                    memoria=prefixo + r.memoria)
    campos = ("descricao", "mes", "instalacao_id", "anexo_id", "evidencia", "quantidade", "tco2e",
              "tco2_biogenico", "memoria")
    con.execute(f"UPDATE atividade SET {', '.join(c + '=?' for c in campos)} WHERE id=?",
                (*(novo[c] for c in campos), atividade_id))
    registrar(con, usuario_id, "editar", "atividade", atividade_id, antes=antes,
              depois={c: novo[c] for c in campos})


def remover_atividade(con: sqlite3.Connection, usuario_id: int, atividade_id: int) -> None:
    antes = buscar(con, "atividade", atividade_id)
    _exigir_edicao(con, usuario_id, buscar(con, "inventario", antes["inventario_id"]))
    con.execute("DELETE FROM atividade WHERE id=?", (atividade_id,))
    registrar(con, usuario_id, "remover", "atividade", atividade_id, antes=antes)


# ---------------------------------------------------------------- totais
def totais(con: sqlite3.Connection, inventario_id: int) -> dict:
    """Totais SÓ das atividades comprovadas. Pendentes e evitadas aparecem à parte."""
    buscar(con, "inventario", inventario_id)
    linhas = con.execute(
        f"""SELECT escopo, COALESCE(SUM(tco2e),0) t, COALESCE(SUM(tco2_biogenico),0) b
            FROM atividade a WHERE inventario_id = ? AND natureza = 'emissao' AND {COMPROVADA}
            GROUP BY escopo""", (inventario_id,)).fetchall()
    soma = lambda cond: con.execute(  # noqa: E731
        f"SELECT COALESCE(SUM(tco2e),0), COUNT(*) FROM atividade a WHERE inventario_id=? AND {cond}",
        (inventario_id,)).fetchone()
    remocoes = soma(f"natureza='remocao' AND {COMPROVADA}")[0]
    evitadas = soma(f"natureza='evitada' AND {COMPROVADA}")[0]
    pend_t, pend_n = soma(f"NOT {COMPROVADA}")
    por = {f"escopo{e}": 0.0 for e in (1, 2, 3)}
    bio = 0.0
    for l in linhas:
        por[f"escopo{l['escopo']}"] = round(l["t"], 6)
        bio += l["b"]
    por["total"] = round(sum(por[f"escopo{e}"] for e in (1, 2, 3)), 6)
    por["co2_biogenico"] = round(bio, 6)
    por["remocoes"] = round(remocoes, 6)
    # Emissões líquidas do escopo 1: base da conciliação periódica (art. 2º, V e XI; art. 34).
    por["escopo1_liquido"] = round(max(por["escopo1"] - remocoes, 0.0), 6)
    por["emissoes_evitadas"] = round(evitadas, 6)
    por["pendentes_comprovacao"] = {"quantidade": pend_n, "tco2e": round(pend_t, 6)}
    return por


def totais_por_instalacao(con: sqlite3.Connection, inventario_id: int) -> list[dict]:
    """Escopo 1 comprovado, bruto e líquido, por instalação ou fonte (unidades organizacionais
    não são instalações para o SBCE). Atividades sem instalação vêm com id None."""
    inv = buscar(con, "inventario", inventario_id)
    linhas = con.execute(
        f"""SELECT i.id, i.nome, i.natureza, i.agropecuaria_primaria, i.residuos_neutralizacao,
                  COALESCE(SUM(CASE WHEN a.natureza='emissao' AND a.escopo=1 THEN a.tco2e END),0) e,
                  COALESCE(SUM(CASE WHEN a.natureza='remocao' THEN a.tco2e END),0) r
           FROM instalacao i LEFT JOIN atividade a ON a.instalacao_id=i.id AND a.inventario_id=?
                AND {COMPROVADA}
           WHERE i.organizacao_id=? AND i.natureza IN ('instalacao','fonte_movel')
           GROUP BY i.id ORDER BY i.id""",
        (inventario_id, inv["organizacao_id"])).fetchall()
    out = [{"instalacao_id": l["id"], "nome": l["nome"], "natureza": l["natureza"],
            "agropecuaria_primaria": bool(l["agropecuaria_primaria"]),
            "residuos_neutralizacao": bool(l["residuos_neutralizacao"]),
            "escopo1": round(l["e"], 6), "remocoes": round(l["r"], 6),
            "escopo1_liquido": round(max(l["e"] - l["r"], 0.0), 6)} for l in linhas]
    sem = con.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN a.natureza='emissao' AND a.escopo=1 THEN a.tco2e END),0) e,
                  COALESCE(SUM(CASE WHEN a.natureza='remocao' THEN a.tco2e END),0) r
           FROM atividade a LEFT JOIN instalacao i ON i.id = a.instalacao_id
           WHERE a.inventario_id=? AND {COMPROVADA}
             AND (a.instalacao_id IS NULL OR i.natureza = 'unidade')""", (inventario_id,)).fetchone()
    if sem["e"] or sem["r"]:
        out.append({"instalacao_id": None, "nome": "(sem instalação)", "natureza": None,
                    "agropecuaria_primaria": False, "residuos_neutralizacao": False,
                    "escopo1": round(sem["e"], 6), "remocoes": round(sem["r"], 6),
                    "escopo1_liquido": round(max(sem["e"] - sem["r"], 0.0), 6)})
    return out


# ---------------------------------------------------------------- fluxo de aprovação
def transitar(con: sqlite3.Connection, usuario_id: int, inventario_id: int, acao: str,
              parecer: str | None = None) -> str:
    with transacao(con):
        inv = buscar(con, "inventario", inventario_id)
        u = buscar(con, "usuario", usuario_id)
        chave = (inv["status"], acao)
        if chave not in TRANSICOES:
            raise ErroNegocio(f"Ação '{acao}' não permitida em '{inv['status']}'.")
        novo, papel = TRANSICOES[chave]
        if u["papel"] != papel:
            raise NaoAutorizado(f"A ação '{acao}' exige o papel '{papel}'.")
        _checar_segregacao(con, u, inv, acao)
        if acao == "devolver" and not (parecer and parecer.strip()):
            raise ErroNegocio("Devolução exige parecer fundamentado.")
        if acao == "submeter":
            n = con.execute(f"SELECT COUNT(*) FROM atividade a WHERE inventario_id=? AND {COMPROVADA}",
                            (inventario_id,)).fetchone()[0]
            if n == 0:
                raise ErroNegocio("Inventário sem atividades comprovadas (com anexo) não pode ser submetido.")
        campos = {"assumir": "verificado_por", "aprovar": "moderado_por"}
        if acao in campos:
            con.execute(f"UPDATE inventario SET {campos[acao]}=? WHERE id=?", (usuario_id, inventario_id))
        con.execute("UPDATE inventario SET status=? WHERE id=?", (novo, inventario_id))
        con.execute("""INSERT INTO evento_mrv (inventario_id, de_status, para_status, usuario_id, parecer)
                       VALUES (?,?,?,?,?)""", (inventario_id, inv["status"], novo, usuario_id, parecer))
        registrar(con, usuario_id, acao, "inventario", inventario_id,
                  antes={"status": inv["status"]}, depois={"status": novo, "parecer": parecer})
    return novo


def _checar_segregacao(con, u, inv, acao) -> None:
    if u["papel"] == "elaborador":
        if u["organizacao_id"] != inv["organizacao_id"]:
            raise NaoAutorizado("Elaborador de outra organização.")
        return
    if u["id"] == inv["elaborado_por"]:
        raise NaoAutorizado("Quem elaborou não pode verificar nem moderar.")
    if u["papel"] == "verificador":
        if not u["acreditado"]:
            raise NaoAutorizado("Verificador precisa ser organismo de inspeção acreditado "
                                "(Lei 15.042/2024, art. 32, parágrafo único).")
        if u["organizacao_id"] == inv["organizacao_id"]:
            raise NaoAutorizado("Verificador não pode pertencer à organização inventariada.")
        if acao != "assumir" and inv["verificado_por"] != u["id"]:
            raise NaoAutorizado("Só o verificador que assumiu o inventário pode concluí-lo.")
    if u["papel"] == "moderador" and u["id"] == inv["verificado_por"]:
        raise NaoAutorizado("Quem verificou não pode moderar.")


def historico(con: sqlite3.Connection, inventario_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT e.*, u.nome AS usuario_nome, u.papel AS usuario_papel FROM evento_mrv e
           JOIN usuario u ON u.id = e.usuario_id WHERE e.inventario_id=? ORDER BY e.id""",
        (inventario_id,))]
