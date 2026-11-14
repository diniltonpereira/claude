"""Projetos de geração de créditos (fase 3): cadastro, funil com checklist e travas legais.

A plataforma ORGANIZA o projeto e guarda as evidências. Validação, verificação e emissão são
feitas pela certificadora e por organismos independentes (Lei 15.042/2024, art. 44, II).

Travas para avançar de etapa (Lei 15.042/2024):
- Área de propriedade e usufruto públicos: o projeto é do ente (art. 43, I a III). Parceria com
  desenvolvedor privado só por licitação (art. 43, §§ 1º e 3º). Ente público não desenvolve
  projeto em área pública com usufruto de terceiro, pois o crédito é do usufrutuário (art. 43, IX).
- Área pública: anuência prévia (ou comunicação, se o usufruto é de terceiro) ao órgão gestor (art. 49).
- Terras indígenas, quilombolas, de comunidades tradicionais e assentamentos: consulta livre,
  prévia e informada (art. 47, I, a) e, em projetos de remoção, ao menos 50% dos créditos para a
  comunidade (art. 47, I, b).
- Contrato entre gerador e desenvolvedor: averbação no registro de imóveis, exceto em projetos
  públicos (art. 43, § 5º).
"""
from __future__ import annotations

import sqlite3

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, NaoEncontrado, buscar, transacao
from .enquadramento import checar_reparticao_comunidade
from .registro import abrir_conta

ETAPAS = ("ideia", "viabilidade", "concepcao", "validacao", "verificacao", "emissao", "venda")
NOME_ETAPA = {"ideia": "Ideia", "viabilidade": "Viabilidade", "concepcao": "Documento de concepção",
              "validacao": "Validação", "verificacao": "Verificação", "emissao": "Emissão", "venda": "Venda"}
CHECKLIST = {
    "ideia": ["Descrição do projeto e da linha de base", "Estimativa preliminar de tCO2e por ano"],
    "viabilidade": ["Estudo de viabilidade técnica e econômica", "Comprovação de titularidade (matrícula, CAR ou cessão)",
                    "Análise de adicionalidade"],
    "concepcao": ["Documento de concepção do projeto (PDD)", "Escolha da metodologia e do padrão certificador"],
    "validacao": ["Contratação do organismo validador independente", "Relatório de validação"],
    "verificacao": ["Relatório de monitoramento", "Relatório de verificação independente"],
    "emissao": ["Comprovante de emissão dos créditos na certificadora"],
    "venda": [],
}
TIPOS = {"biogas": "Biogás e biometano", "solar": "Energia solar", "eficiencia": "Eficiência energética",
         "restauracao_psa": "Restauração florestal e PSA", "residuos": "Manejo de resíduos",
         "hidrogenio": "Hidrogênio verde"}
AREAS_PUBLICAS = ("publica_propria", "publica_terceiros")
AREAS_COMUNIDADES = ("terra_indigena", "quilombola", "comunidade_tradicional", "assentamento")


def _titular(con, usuario_id: int, projeto: sqlite3.Row) -> sqlite3.Row:
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] not in ("elaborador", "fornecedor") or u["organizacao_id"] != projeto["titular_id"]:
        raise NaoAutorizado("Ação restrita ao titular do projeto.")
    return u


def criar(con: sqlite3.Connection, usuario_id: int, *, nome: str, tipo: str, natureza_area: str, municipio: str,
          uf: str, latitude: float, longitude: float, linha_base: str, estimativa_tco2e_ano: float,
          area_ha: float | None = None, matricula: str | None = None, car: str | None = None) -> int:
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] not in ("elaborador", "fornecedor") or not u["organizacao_id"]:
        raise NaoAutorizado("Só o gestor de uma organização cadastra projetos.")
    org = buscar(con, "organizacao", u["organizacao_id"])
    publico = org["tipo"] == "orgao_publico"
    if tipo not in TIPOS:
        raise ErroNegocio("Tipo de projeto inválido.")
    if publico and natureza_area == "publica_terceiros":
        raise ErroNegocio("Em área pública com usufruto de terceiro, o crédito pertence ao usufrutuário "
                          "(art. 43, IX): o ente não pode ser o titular do projeto.")
    if not publico and natureza_area == "publica_propria":
        raise ErroNegocio("Em área de propriedade e usufruto públicos, o titular é o ente público (art. 43, I a III). "
                          "A empresa participa como desenvolvedora, após licitação.")
    if tipo == "restauracao_psa" and natureza_area in ("privada", "assentamento") and not (car or "").strip():
        raise ErroNegocio("Informe o CAR do imóvel rural (Lei 12.651/2012).")
    try:
        pid = con.execute(
            """INSERT INTO projeto (titular_id, nome, tipo, natureza_area, municipio, uf, latitude, longitude, area_ha,
               matricula, car, linha_base, estimativa_tco2e_ano, criado_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (org["id"], nome, tipo, natureza_area, municipio, uf.upper(), latitude, longitude, area_ha, matricula, car,
             linha_base, estimativa_tco2e_ano, usuario_id)).lastrowid
    except sqlite3.IntegrityError as exc:
        raise ErroNegocio(f"Dados do projeto inválidos (coordenadas fora do Brasil?): {exc}") from exc
    con.executemany("INSERT INTO projeto_item (projeto_id, etapa, item) VALUES (?,?,?)",
                    [(pid, e, i) for e, itens in CHECKLIST.items() for i in itens])
    registrar(con, usuario_id, "criar", "projeto", pid, depois={"nome": nome, "tipo": tipo, "area": natureza_area})
    return pid


def atualizar(con: sqlite3.Connection, usuario_id: int, projeto_id: int, **campos) -> None:
    """Registra anuência, consulta, certificadora, nº de registro e lote de venda."""
    p = buscar(con, "projeto", projeto_id)
    _titular(con, usuario_id, p)
    permitidos = {"anuencia_ref", "consulta_anexo_id", "certificadora", "registro_ref", "lote_serial"}
    dados = {k: v for k, v in campos.items() if k in permitidos and v not in (None, "")}
    if not dados:
        return
    if "consulta_anexo_id" in dados:
        from .anexos import exigir_acesso
        exigir_acesso(con, usuario_id, dados["consulta_anexo_id"])
    con.execute(f"UPDATE projeto SET {', '.join(k + '=?' for k in dados)} WHERE id=?", (*dados.values(), projeto_id))
    registrar(con, usuario_id, "atualizar", "projeto", projeto_id, antes={k: p[k] for k in dados}, depois=dados)


def concluir_item(con: sqlite3.Connection, usuario_id: int, item_id: int, *, anexo_id: int,
                  responsavel: str) -> None:
    it = buscar(con, "projeto_item", item_id)
    p = buscar(con, "projeto", it["projeto_id"])
    _titular(con, usuario_id, p)
    if ETAPAS.index(it["etapa"]) > ETAPAS.index(p["etapa"]):
        raise ErroNegocio("Este item pertence a uma etapa futura.")
    if not responsavel.strip():
        raise ErroNegocio("Informe o responsável.")
    from .anexos import exigir_acesso
    exigir_acesso(con, usuario_id, anexo_id)
    con.execute("""UPDATE projeto_item SET concluido=1, anexo_id=?, responsavel=?,
                   concluido_em=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""", (anexo_id, responsavel, item_id))
    registrar(con, usuario_id, "concluir_item", "projeto", p["id"], depois={"item": it["item"], "anexo": anexo_id})


def registrar_parceria(con: sqlite3.Connection, usuario_id: int, projeto_id: int, *, desenvolvedor_cnpj: str,
                       desenvolvedor_nome: str, percentual_titular: float, percentual_desenvolvedor: float,
                       percentual_comunidade: float = 0, licitacao_ref: str | None = None,
                       averbacao_ref: str | None = None) -> int:
    with transacao(con):
        p = buscar(con, "projeto", projeto_id)
        _titular(con, usuario_id, p)
        if abs(percentual_titular + percentual_desenvolvedor + percentual_comunidade - 100) > 0.001:
            raise ErroNegocio("A divisão dos créditos deve somar 100%.")
        org = buscar(con, "organizacao", p["titular_id"])
        if org["tipo"] == "orgao_publico" and p["natureza_area"] == "publica_propria" and not (licitacao_ref or "").strip():
            raise ErroNegocio("Projeto público com desenvolvedor privado exige licitação (art. 43, §§ 1º e 3º). "
                              "Informe o processo licitatório.")
        if p["natureza_area"] in AREAS_COMUNIDADES and p["tipo"] == "restauracao_psa":
            falhas = checar_reparticao_comunidade("remocao", percentual_comunidade)
            if falhas:
                raise ErroNegocio(falhas[0])
        if not (len(desenvolvedor_cnpj) == 14 and desenvolvedor_cnpj.isdigit()):
            raise ErroNegocio("CNPJ do desenvolvedor deve ter 14 dígitos.")
        d = con.execute("SELECT id FROM organizacao WHERE cnpj=?", (desenvolvedor_cnpj,)).fetchone()
        dev = d["id"] if d else con.execute("INSERT INTO organizacao (nome, cnpj, tipo) VALUES (?,?,'empresa')",
                                            (desenvolvedor_nome, desenvolvedor_cnpj)).lastrowid
        abrir_conta(con, dev)
        if dev == p["titular_id"]:
            raise ErroNegocio("O desenvolvedor deve ser outra organização.")
        try:
            pid = con.execute(
                """INSERT INTO parceria (projeto_id, desenvolvedor_id, percentual_titular, percentual_desenvolvedor,
                   percentual_comunidade, licitacao_ref, averbacao_ref) VALUES (?,?,?,?,?,?,?)""",
                (projeto_id, dev, percentual_titular, percentual_desenvolvedor, percentual_comunidade,
                 licitacao_ref, averbacao_ref)).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ErroNegocio("O projeto já tem parceria registrada.") from exc
        registrar(con, usuario_id, "registrar_parceria", "projeto", projeto_id,
                  depois={"desenvolvedor": desenvolvedor_cnpj, "titular": percentual_titular,
                          "desenvolvedor_pct": percentual_desenvolvedor, "comunidade": percentual_comunidade})
    return pid


def assinar_parceria(con: sqlite3.Connection, usuario_id: int, projeto_id: int, *, assinatura_ref: str,
                     averbacao_ref: str | None = None) -> None:
    p = buscar(con, "projeto", projeto_id)
    _titular(con, usuario_id, p)
    par = con.execute("SELECT * FROM parceria WHERE projeto_id=?", (projeto_id,)).fetchone()
    if par is None:
        raise ErroNegocio("Não há parceria registrada.")
    if not assinatura_ref.strip():
        raise ErroNegocio("Informe a referência da assinatura eletrônica (gov.br ou Clicksign).")
    con.execute("UPDATE parceria SET assinatura='assinado', assinatura_ref=?, averbacao_ref=COALESCE(?, averbacao_ref) "
                "WHERE id=?", (assinatura_ref.strip(), averbacao_ref, par["id"]))
    registrar(con, usuario_id, "assinar_parceria", "projeto", projeto_id, depois={"assinatura": assinatura_ref})


def pendencias(con: sqlite3.Connection, projeto_id: int) -> list[str]:
    """O que impede o projeto de avançar para a próxima etapa."""
    p = buscar(con, "projeto", projeto_id)
    if p["etapa"] == "venda":
        return ["O projeto já está na última etapa."]
    proxima = ETAPAS[ETAPAS.index(p["etapa"]) + 1]
    out = [f"Concluir com evidência: {r['item']}" for r in con.execute(
        "SELECT item FROM projeto_item WHERE projeto_id=? AND etapa=? AND concluido=0 ORDER BY id",
        (projeto_id, p["etapa"]))]
    par = con.execute("SELECT * FROM parceria WHERE projeto_id=?", (projeto_id,)).fetchone()
    idx = ETAPAS.index(proxima)
    if idx >= ETAPAS.index("concepcao"):
        if p["natureza_area"] in AREAS_COMUNIDADES and not p["consulta_anexo_id"]:
            out.append("Anexar a ata da consulta livre, prévia e informada à comunidade (art. 47, I, a).")
        if p["tipo"] == "restauracao_psa" and p["natureza_area"] in AREAS_COMUNIDADES and not par:
            out.append("Registrar a parceria com a repartição dos créditos com a comunidade (art. 47, I, b).")
    if idx >= ETAPAS.index("validacao"):
        if p["natureza_area"] in AREAS_PUBLICAS and not p["anuencia_ref"]:
            out.append("Registrar a anuência (ou comunicação) do órgão gestor da área (art. 49).")
        if par and par["assinatura"] != "assinado":
            out.append("Assinar o contrato de parceria (assinatura eletrônica).")
        if par and p["natureza_area"] != "publica_propria" and not par["averbacao_ref"]:
            out.append("Averbar o contrato de parceria no registro de imóveis (art. 43, § 5º).")
    if idx >= ETAPAS.index("emissao") and not (p["certificadora"] and p["registro_ref"]):
        out.append("Informar a certificadora e o nº de registro do projeto (a plataforma não emite créditos).")
    if proxima == "venda":
        if not p["lote_serial"]:
            out.append("Informar o serial do lote emitido.")
        elif not con.execute("SELECT 1 FROM lote WHERE serial=?", (p["lote_serial"],)).fetchone():
            out.append("O lote informado ainda não foi escriturado no registro da plataforma.")
    return out


def avancar(con: sqlite3.Connection, usuario_id: int, projeto_id: int) -> str:
    with transacao(con):
        p = buscar(con, "projeto", projeto_id)
        _titular(con, usuario_id, p)
        pend = pendencias(con, projeto_id)
        if pend:
            raise ErroNegocio("Pendências para avançar: " + " | ".join(pend))
        proxima = ETAPAS[ETAPAS.index(p["etapa"]) + 1]
        con.execute("UPDATE projeto SET etapa=? WHERE id=?", (proxima, projeto_id))
        registrar(con, usuario_id, "avancar", "projeto", projeto_id, antes={"etapa": p["etapa"]},
                  depois={"etapa": proxima})
    return proxima


def listar(con: sqlite3.Connection, usuario_id: int) -> list[dict]:
    u = buscar(con, "usuario", usuario_id)
    sql = """SELECT p.*, o.nome AS titular FROM projeto p JOIN organizacao o ON o.id=p.titular_id"""
    if u["papel"] in ("registrador", "auditor"):
        return [dict(r) for r in con.execute(sql + " ORDER BY p.id DESC")]
    return [dict(r) for r in con.execute(
        sql + """ WHERE p.titular_id=? OR p.id IN (SELECT projeto_id FROM parceria WHERE desenvolvedor_id=?)
                  ORDER BY p.id DESC""", (u["organizacao_id"], u["organizacao_id"]))]


def ver(con: sqlite3.Connection, usuario_id: int, projeto_id: int) -> dict:
    p = con.execute("SELECT p.*, o.nome AS titular, o.tipo AS titular_tipo FROM projeto p "
                    "JOIN organizacao o ON o.id=p.titular_id WHERE p.id=?", (projeto_id,)).fetchone()
    if p is None:
        raise NaoEncontrado("Projeto não encontrado.")
    u = buscar(con, "usuario", usuario_id)
    par = con.execute("SELECT pa.*, o.nome AS desenvolvedor, o.cnpj AS desenvolvedor_cnpj FROM parceria pa "
                      "JOIN organizacao o ON o.id=pa.desenvolvedor_id WHERE pa.projeto_id=?", (projeto_id,)).fetchone()
    if not (u["papel"] in ("registrador", "auditor") or u["organizacao_id"] == p["titular_id"]
            or (par and u["organizacao_id"] == par["desenvolvedor_id"])):
        raise NaoAutorizado("Projeto de outra organização.")
    itens = [dict(r) for r in con.execute("SELECT * FROM projeto_item WHERE projeto_id=? ORDER BY id", (projeto_id,))]
    return {**dict(p), "itens": itens, "parceria": dict(par) if par else None,
            "pendencias": pendencias(con, projeto_id)}
