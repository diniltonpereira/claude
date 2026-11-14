"""Gestão de terceirizadas: contratos, lançamento mensal, score, selo e notificações.

Regras jurídicas embutidas:
- Atraso no relato só vira "inadimplente" se o contrato (ou aditivo) tiver cláusula de relato
  (`clausula_relato`). Sem cláusula, a situação é apenas "sem lançamento", sem efeito contratual.
- O score é informativo e só pode servir de critério de licitação se o edital previr regra
  objetiva (Lei 14.133/2021). A plataforma não aplica sanção: gera documentos para o processo.
- O selo se chama "Selo de Gestão de Emissões" e NÃO afirma neutralidade ou descarbonização
  (CDC, art. 37). Classe D não recebe selo. Validade de 12 meses.
- Emissões da terceirizada entram no inventário do órgão como escopo 3, só as comprovadas.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import secrets
import sqlite3
from datetime import date, timedelta
from typing import Protocol

from . import fatores
from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, buscar, transacao
from .calculo import calcular_por_fator
from .registro import abrir_conta

PESOS = {"regularidade": 40, "reducao": 30, "compensacao": 20, "acoes": 10}
CLASSES = (("A", 80), ("B", 60), ("C", 40), ("D", 0))


# ---------------------------------------------------------------- utilidades de data
def _mes_seguinte(ano: int, mes: int) -> tuple[int, int]:
    return (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def _meses(inicio: date, fim: date) -> list[tuple[int, int]]:
    out, (a, m) = [], (inicio.year, inicio.month)
    while (a, m) <= (fim.year, fim.month):
        out.append((a, m))
        a, m = _mes_seguinte(a, m)
    return out


def _prazo(ano: int, mes: int, dia_limite: int) -> date:
    a, m = _mes_seguinte(ano, mes)
    return date(a, m, dia_limite)


def classe_de(score: float) -> str:
    return next(c for c, minimo in CLASSES if score >= minimo)


# ---------------------------------------------------------------- permissões
def _usuario(con, uid):
    return buscar(con, "usuario", uid)


def _exigir_orgao(con, uid: int, contrato: sqlite3.Row, papeis=("elaborador", "fiscal")) -> sqlite3.Row:
    u = _usuario(con, uid)
    if u["papel"] not in papeis or u["organizacao_id"] != contrato["orgao_id"]:
        raise NaoAutorizado("Ação restrita ao órgão contratante (gestor ou fiscal do contrato).")
    return u


def _exigir_parte(con, uid: int, contrato: sqlite3.Row) -> sqlite3.Row:
    """Fornecedor do contrato ou fiscal do órgão contratante."""
    u = _usuario(con, uid)
    if u["papel"] == "fornecedor" and u["organizacao_id"] == contrato["fornecedor_id"]:
        return u
    if u["papel"] == "fiscal" and u["organizacao_id"] == contrato["orgao_id"]:
        return u
    raise NaoAutorizado("Só a terceirizada do contrato ou o fiscal do órgão podem lançar.")


def pode_ver(con, uid: int, contrato: sqlite3.Row) -> bool:
    u = _usuario(con, uid)
    return (u["papel"] in ("registrador", "auditor")
            or u["organizacao_id"] in (contrato["orgao_id"], contrato["fornecedor_id"]))


# ---------------------------------------------------------------- contratos
def convidar(con: sqlite3.Connection, usuario_id: int, *, orgao_id: int, fornecedor_cnpj: str,
             fornecedor_nome: str, numero: str, objeto: str, vigencia_inicio: str, vigencia_fim: str,
             clausula_relato: bool = False, dia_limite: int = 10) -> int:
    u = _usuario(con, usuario_id)
    if u["papel"] not in ("elaborador", "fiscal") or u["organizacao_id"] != orgao_id:
        raise NaoAutorizado("Só o gestor ou o fiscal do órgão convidam terceirizadas.")
    if not (len(fornecedor_cnpj) == 14 and fornecedor_cnpj.isdigit()):
        raise ErroNegocio("CNPJ deve ter 14 dígitos.")
    try:
        ini, fim = date.fromisoformat(vigencia_inicio), date.fromisoformat(vigencia_fim)
    except ValueError as exc:
        raise ErroNegocio("Vigência em formato AAAA-MM-DD.") from exc
    if fim <= ini:
        raise ErroNegocio("O fim da vigência deve ser posterior ao início.")
    with transacao(con):
        f = con.execute("SELECT id FROM organizacao WHERE cnpj=?", (fornecedor_cnpj,)).fetchone()
        if f:
            fid = f["id"]
        else:
            fid = con.execute("INSERT INTO organizacao (nome, cnpj, tipo) VALUES (?,?,'empresa')",
                              (fornecedor_nome, fornecedor_cnpj)).lastrowid
            abrir_conta(con, fid)
        if fid == orgao_id:
            raise ErroNegocio("O órgão não pode contratar a si mesmo.")
        try:
            cid = con.execute(
                """INSERT INTO contrato (orgao_id, fornecedor_id, numero, objeto, vigencia_inicio,
                   vigencia_fim, clausula_relato, dia_limite) VALUES (?,?,?,?,?,?,?,?)""",
                (orgao_id, fid, numero, objeto, vigencia_inicio, vigencia_fim, int(clausula_relato),
                 dia_limite)).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ErroNegocio(f"Contrato {numero} já cadastrado para este órgão ou dados inválidos.") from exc
        registrar(con, usuario_id, "convidar", "contrato", cid,
                  depois={"numero": numero, "fornecedor_cnpj": fornecedor_cnpj,
                          "clausula_relato": clausula_relato})
    return cid


def aceitar(con: sqlite3.Connection, usuario_id: int, contrato_id: int) -> None:
    c = buscar(con, "contrato", contrato_id)
    u = _usuario(con, usuario_id)
    if u["papel"] != "fornecedor" or u["organizacao_id"] != c["fornecedor_id"]:
        raise NaoAutorizado("Só a terceirizada convidada aceita o convite.")
    if c["aceito_em"]:
        return
    con.execute("UPDATE contrato SET aceito_em=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (contrato_id,))
    registrar(con, usuario_id, "aceitar", "contrato", contrato_id)


def listar(con: sqlite3.Connection, usuario_id: int) -> list[dict]:
    u = _usuario(con, usuario_id)
    sql = """SELECT c.*, o.nome AS orgao, f.nome AS fornecedor, f.cnpj AS fornecedor_cnpj,
                    (SELECT classe FROM score_fornecedor s WHERE s.contrato_id=c.id
                     ORDER BY ano DESC, mes DESC LIMIT 1) AS classe_atual
             FROM contrato c JOIN organizacao o ON o.id=c.orgao_id JOIN organizacao f ON f.id=c.fornecedor_id"""
    if u["papel"] in ("registrador", "auditor"):
        return [dict(r) for r in con.execute(sql + " ORDER BY c.id")]
    return [dict(r) for r in con.execute(sql + " WHERE c.orgao_id=? OR c.fornecedor_id=? ORDER BY c.id",
                                         (u["organizacao_id"], u["organizacao_id"]))]


# ---------------------------------------------------------------- lançamentos e ações
def lancar(con: sqlite3.Connection, usuario_id: int, contrato_id: int, *, ano: int, mes: int,
           descricao: str, fator: str, quantidade: float, anexo_id: int | None = None,
           hoje: date | None = None) -> int:
    hoje = hoje or date.today()
    c = buscar(con, "contrato", contrato_id)
    u = _exigir_parte(con, usuario_id, c)
    if u["papel"] == "fornecedor" and not c["aceito_em"]:
        raise ErroNegocio("Aceite o convite do contrato antes de lançar.")
    if not 1 <= mes <= 12:
        raise ErroNegocio("Mês inválido.")
    inicio = date.fromisoformat(c["vigencia_inicio"]).replace(day=1)
    fim = date.fromisoformat(c["vigencia_fim"])
    if not inicio <= date(ano, mes, 1) <= fim:
        raise ErroNegocio("Mês fora da vigência do contrato.")
    if date(ano, mes, 1) > hoje:
        raise ErroNegocio("Não é possível lançar mês futuro.")
    if anexo_id is not None:
        from .anexos import exigir_acesso
        exigir_acesso(con, usuario_id, anexo_id)
    f = fatores.vigente(con, fator)
    try:
        r = calcular_por_fator(f, quantidade)
    except ValueError as exc:
        raise ErroNegocio(str(exc)) from exc
    pelo_fiscal = u["papel"] == "fiscal"
    lid = con.execute(
        """INSERT INTO lancamento_fornecedor (contrato_id, ano, mes, descricao, fator_id, quantidade, tco2e,
           memoria, anexo_id, lancado_por, lancado_pelo_fiscal) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (contrato_id, ano, mes, descricao, f.id, quantidade, r.tco2e,
         ("LANÇADO PELO FISCAL: " if pelo_fiscal else "") + r.memoria, anexo_id, usuario_id,
         int(pelo_fiscal))).lastrowid
    registrar(con, usuario_id, "lancar", "lancamento_fornecedor", lid,
              depois={"contrato_id": contrato_id, "ano": ano, "mes": mes, "tco2e": r.tco2e,
                      "pelo_fiscal": pelo_fiscal})
    return lid


def registrar_acao(con: sqlite3.Connection, usuario_id: int, contrato_id: int, *, data: str,
                   descricao: str, anexo_id: int) -> int:
    c = buscar(con, "contrato", contrato_id)
    _exigir_parte(con, usuario_id, c)
    from .anexos import exigir_acesso
    exigir_acesso(con, usuario_id, anexo_id)
    date.fromisoformat(data)
    aid = con.execute("""INSERT INTO acao_fornecedor (contrato_id, data, descricao, anexo_id, registrado_por)
                         VALUES (?,?,?,?,?)""", (contrato_id, data, descricao, anexo_id, usuario_id)).lastrowid
    registrar(con, usuario_id, "registrar_acao", "acao_fornecedor", aid, depois={"descricao": descricao})
    return aid


def lancamentos(con: sqlite3.Connection, contrato_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT l.*, u.nome AS lancado_por_nome FROM lancamento_fornecedor l
           JOIN usuario u ON u.id = l.lancado_por WHERE contrato_id=? ORDER BY ano DESC, mes DESC, l.id DESC""",
        (contrato_id,))]


def situacao_mensal(con: sqlite3.Connection, contrato_id: int, hoje: date | None = None) -> list[dict]:
    """Situação de cada mês da vigência até hoje."""
    hoje = hoje or date.today()
    c = buscar(con, "contrato", contrato_id)
    ini = date.fromisoformat(c["vigencia_inicio"])
    fim = min(date.fromisoformat(c["vigencia_fim"]), hoje)
    if fim < ini:
        return []
    feitos = {(r["ano"], r["mes"]): r for r in con.execute(
        """SELECT ano, mes, COUNT(*) n, SUM(anexo_id IS NOT NULL) comprovados,
                  MAX(lancado_pelo_fiscal) fiscal, COALESCE(SUM(CASE WHEN anexo_id IS NOT NULL THEN tco2e END),0) t
           FROM lancamento_fornecedor WHERE contrato_id=? GROUP BY ano, mes""", (contrato_id,))}
    out = []
    for a, m in _meses(ini, fim):
        f = feitos.get((a, m))
        prazo = _prazo(a, m, c["dia_limite"])
        if f and f["comprovados"]:
            st = "lancado_pelo_fiscal" if f["fiscal"] else "lancado"
        elif f:
            st = "pendente_comprovacao"
        elif hoje <= prazo:
            st = "em_prazo"
        else:
            st = "inadimplente" if c["clausula_relato"] else "sem_lancamento"
        out.append({"ano": a, "mes": m, "prazo": prazo.isoformat(), "situacao": st,
                    "tco2e_comprovado": round(f["t"], 6) if f else 0.0})
    return out


# ---------------------------------------------------------------- score
def _soma(con, contrato_id: int, meses: list[tuple[int, int]]) -> float:
    if not meses:
        return 0.0
    cond = " OR ".join("(ano=? AND mes=?)" for _ in meses)
    args = [x for am in meses for x in am]
    return con.execute(
        f"""SELECT COALESCE(SUM(tco2e),0) FROM lancamento_fornecedor
            WHERE contrato_id=? AND anexo_id IS NOT NULL AND ({cond})""", (contrato_id, *args)).fetchone()[0]


def calcular_score(con: sqlite3.Connection, contrato_id: int, hoje: date | None = None,
                   gravar: bool = True) -> dict:
    """Score de descarbonização 0–100 (40 regularidade, 30 redução, 20 compensação, 10 ações).

    - Regularidade: meses vencidos dos últimos 12 com lançamento comprovado (com anexo).
    - Redução: 12 meses mais recentes vs. os 12 primeiros meses do contrato. Sem 24 meses de
      histórico, recebe metade dos pontos (neutro). Pontuação cheia com 10% de redução; zero com
      aumento de 10% ou mais (linear entre esses pontos).
    - Compensação: créditos cancelados pela terceirizada (compensação voluntária) nos últimos
      12 meses, em proporção às emissões comprovadas do período (limitado a 100%).
    - Ações: 2,5 pontos por ação com evidência nos últimos 12 meses (máximo 4).
    """
    hoje = hoje or date.today()
    c = buscar(con, "contrato", contrato_id)
    sit = situacao_mensal(con, contrato_id, hoje)
    vencidos = [s for s in sit if s["situacao"] not in ("em_prazo",)][-12:]
    ok = [s for s in vencidos if s["situacao"] in ("lancado", "lancado_pelo_fiscal")]
    regularidade = len(ok) / len(vencidos) if vencidos else 1.0

    janela = [(s["ano"], s["mes"]) for s in vencidos]
    todos = [(s["ano"], s["mes"]) for s in sit if s["situacao"] != "em_prazo"]
    atual = _soma(con, contrato_id, janela)
    if len(todos) >= 24:
        base = _soma(con, contrato_id, todos[:12])
        variacao = (base - atual) / base if base else 0.0
        reducao_frac = min(max(0.5 + variacao / 0.2, 0.0), 1.0)
        nota_reducao = f"base {base:.3f} t, atual {atual:.3f} t, variação {variacao:+.1%}"
    else:
        reducao_frac, nota_reducao = 0.5, "menos de 24 meses de histórico: pontuação neutra"

    desde = (hoje - timedelta(days=365)).isoformat()
    compensado = con.execute(
        """SELECT COALESCE(SUM(m.quantidade),0) FROM movimento m JOIN lote l ON l.id=m.lote_id
           JOIN conta ct ON ct.id = m.de_conta
           WHERE m.tipo='cancelamento' AND l.finalidade='compensacao_voluntaria'
             AND ct.organizacao_id=? AND m.em >= ?""", (c["fornecedor_id"], desde)).fetchone()[0]
    compensacao_frac = min(compensado / atual, 1.0) if atual > 0 else 0.0
    acoes = con.execute("SELECT COUNT(*) FROM acao_fornecedor WHERE contrato_id=? AND data >= ?",
                        (contrato_id, desde)).fetchone()[0]
    acoes_frac = min(acoes, 4) / 4

    pontos = {
        "regularidade": round(PESOS["regularidade"] * regularidade, 2),
        "reducao": round(PESOS["reducao"] * reducao_frac, 2),
        "compensacao": round(PESOS["compensacao"] * compensacao_frac, 2),
        "acoes": round(PESOS["acoes"] * acoes_frac, 2),
    }
    score = round(sum(pontos.values()), 2)
    resultado = {
        "contrato_id": contrato_id, "ano": hoje.year, "mes": hoje.month, "score": score,
        "classe": classe_de(score), "pontos": pontos,
        "memoria": {"meses_vencidos": len(vencidos), "meses_comprovados": len(ok),
                    "reducao": nota_reducao, "emissoes_12m": round(atual, 6),
                    "creditos_compensados_12m": compensado, "acoes_12m": acoes},
    }
    if gravar:
        anterior = con.execute(
            """SELECT classe FROM score_fornecedor WHERE contrato_id=? AND (ano < ? OR (ano = ? AND mes < ?))
               ORDER BY ano DESC, mes DESC LIMIT 1""", (contrato_id, hoje.year, hoje.year, hoje.month)).fetchone()
        con.execute(
            """INSERT INTO score_fornecedor (contrato_id, ano, mes, score, classe, componentes)
               VALUES (?,?,?,?,?,?) ON CONFLICT (contrato_id, ano, mes) DO UPDATE
               SET score=excluded.score, classe=excluded.classe, componentes=excluded.componentes,
                   calculado_em=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            (contrato_id, hoje.year, hoje.month, score, resultado["classe"],
             json.dumps({"pontos": pontos, **resultado["memoria"]}, ensure_ascii=False)))
        caiu = anterior and resultado["classe"] in ("C", "D") and resultado["classe"] > anterior["classe"]
        if caiu:
            _notificar(con, contrato_id, "alerta", "fiscal",
                       f"Score da terceirizada caiu para {resultado['classe']}",
                       f"O score passou de {anterior['classe']} para {resultado['classe']} ({score:g} pontos). "
                       "Verifique os lançamentos e as ações do contrato.",
                       referencia=f"{hoje:%Y-%m}-queda")
        resultado["alerta_queda"] = bool(caiu)
    return resultado


# ---------------------------------------------------------------- selo
def emitir_selo(con: sqlite3.Connection, usuario_id: int | None, contrato_id: int,
                hoje: date | None = None) -> dict:
    """Emite o Selo de Gestão de Emissões a partir do score atual. Classe D não recebe selo."""
    hoje = hoje or date.today()
    c = buscar(con, "contrato", contrato_id)
    if usuario_id is not None:
        _exigir_orgao(con, usuario_id, c)
    if not c["aceito_em"]:
        raise ErroNegocio("A terceirizada ainda não aceitou o contrato.")
    s = calcular_score(con, contrato_id, hoje)
    if s["classe"] == "D":
        raise ErroNegocio(f"Score {s['score']:g} (classe D): não há selo para esta faixa.")
    periodo_fim = hoje
    periodo_ini = max(date.fromisoformat(c["vigencia_inicio"]), hoje - timedelta(days=365))
    valido = hoje.replace(year=hoje.year + 1) if not (hoje.month == 2 and hoje.day == 29) \
        else date(hoje.year + 1, 2, 28)
    codigo = secrets.token_hex(6)
    dados = {"codigo": codigo, "contrato_id": contrato_id, "classe": s["classe"], "score": s["score"],
             "periodo_inicio": periodo_ini.isoformat(), "periodo_fim": periodo_fim.isoformat(),
             "emissoes_tco2e": s["memoria"]["emissoes_12m"], "emitido_em": hoje.isoformat(),
             "valido_ate": valido.isoformat()}
    dados["hash"] = hashlib.sha256(json.dumps(dados, sort_keys=True).encode()).hexdigest()
    con.execute(f"INSERT INTO selo ({', '.join(dados)}) VALUES ({', '.join('?' * len(dados))})",
                tuple(dados.values()))
    registrar(con, usuario_id, "emitir_selo", "contrato", contrato_id, depois=dados)
    return dados


def verificar_selo(con: sqlite3.Connection, codigo: str, hoje: date | None = None) -> dict:
    """Dados públicos do selo (página aberta pelo QR Code)."""
    hoje = hoje or date.today()
    s = con.execute(
        """SELECT s.*, c.numero AS contrato_numero, c.objeto, o.nome AS orgao, f.nome AS fornecedor,
                  f.cnpj AS fornecedor_cnpj
           FROM selo s JOIN contrato c ON c.id=s.contrato_id JOIN organizacao o ON o.id=c.orgao_id
           JOIN organizacao f ON f.id=c.fornecedor_id WHERE s.codigo=?""", (codigo,)).fetchone()
    if s is None:
        from .banco import NaoEncontrado
        raise NaoEncontrado("Selo não encontrado.")
    d = dict(s)
    conferido = {k: d[k] for k in ("codigo", "contrato_id", "classe", "score", "periodo_inicio",
                                   "periodo_fim", "emissoes_tco2e", "emitido_em", "valido_ate")}
    d["integro"] = hashlib.sha256(json.dumps(conferido, sort_keys=True).encode()).hexdigest() == d["hash"]
    d["situacao"] = "valido" if date.fromisoformat(d["valido_ate"]) >= hoje else "expirado"
    d["titulo"] = f"Selo de Gestão de Emissões — Nível {d['classe']}"
    return d


# ---------------------------------------------------------------- notificações
class Notificador(Protocol):
    """Canal de entrega (e-mail, WhatsApp…). Implemente no deploy; exige base legal e opt-in (LGPD)."""
    def enviar(self, destinatario: str, assunto: str, texto: str) -> None: ...


class SemEnvioExterno:
    """Padrão do MVP: a notificação fica registrada no sistema e aparece no painel."""
    def enviar(self, destinatario: str, assunto: str, texto: str) -> None:
        return None


def _notificar(con, contrato_id, tipo, destinatario, assunto, texto, *, prazo=None, anexo_id=None,
               emitida_por=None, referencia=None) -> int | None:
    try:
        return con.execute(
            """INSERT INTO notificacao (contrato_id, tipo, destinatario, assunto, texto, prazo, anexo_id,
               emitida_por, referencia) VALUES (?,?,?,?,?,?,?,?,?)""",
            (contrato_id, tipo, destinatario, assunto, texto, prazo, anexo_id, emitida_por, referencia)).lastrowid
    except sqlite3.IntegrityError:
        return None  # já emitida para esta referência


def notificar(con: sqlite3.Connection, usuario_id: int, contrato_id: int, *, assunto: str, texto: str,
              prazo: str | None = None, anexo_id: int | None = None) -> int:
    """Notificação formal do fiscal à terceirizada. Vira documento para o processo administrativo."""
    c = buscar(con, "contrato", contrato_id)
    _exigir_orgao(con, usuario_id, c, papeis=("fiscal",))
    if prazo:
        date.fromisoformat(prazo)
    nid = _notificar(con, contrato_id, "notificacao_formal", "fornecedor", assunto, texto, prazo=prazo,
                     anexo_id=anexo_id, emitida_por=usuario_id)
    registrar(con, usuario_id, "notificar", "contrato", contrato_id, depois={"assunto": assunto, "prazo": prazo})
    return nid


def notificacoes(con: sqlite3.Connection, contrato_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT * FROM notificacao WHERE contrato_id=? ORDER BY id DESC", (contrato_id,))]


def rotina_diaria(con: sqlite3.Connection, hoje: date | None = None,
                  notificador: Notificador | None = None) -> dict:
    """Executar uma vez por dia (cron). Para cada contrato vigente e aceito:
    - do dia 1 ao prazo: lembrete à terceirizada se o mês anterior ainda não foi lançado;
    - após o prazo sem lançamento: alerta ao fiscal (e marcação de inadimplência se houver cláusula);
    - no primeiro dia do mês: recalcula o score.
    Não repete notificações para o mesmo mês."""
    hoje = hoje or date.today()
    notificador = notificador or SemEnvioExterno()
    resumo = {"lembretes": 0, "alertas": 0, "scores": 0}
    for c in con.execute("SELECT * FROM contrato WHERE aceito_em IS NOT NULL AND vigencia_inicio <= ? "
                         "AND vigencia_fim >= ?", (hoje.isoformat(), (hoje - timedelta(days=40)).isoformat())):
        a, m = (hoje.year - 1, 12) if hoje.month == 1 else (hoje.year, hoje.month - 1)
        if date(a, m, 1) < date.fromisoformat(c["vigencia_inicio"]).replace(day=1):
            continue
        sit = next((s for s in situacao_mensal(con, c["id"], hoje) if (s["ano"], s["mes"]) == (a, m)), None)
        ref = f"{a}-{m:02d}"
        if sit and sit["situacao"] == "em_prazo":
            if _notificar(con, c["id"], "lembrete", "fornecedor", f"Lançar emissões de {m:02d}/{a}",
                          f"Lance as emissões de {m:02d}/{a} do contrato {c['numero']} até {sit['prazo']}, "
                          "com a nota fiscal ou fatura anexada.", prazo=sit["prazo"], referencia=ref):
                notificador.enviar("fornecedor", f"Lançar emissões de {m:02d}/{a}", c["numero"])
                resumo["lembretes"] += 1
        elif sit and sit["situacao"] in ("inadimplente", "sem_lancamento"):
            texto = (f"A terceirizada não lançou as emissões de {m:02d}/{a} até {sit['prazo']}. "
                     + ("O contrato prevê a obrigação de relato: avalie notificação formal."
                        if c["clausula_relato"] else "O contrato não tem cláusula de relato: situação informativa."))
            if _notificar(con, c["id"], "alerta", "fiscal", f"Sem lançamento em {m:02d}/{a}", texto,
                          referencia=ref + "-atraso"):
                notificador.enviar("fiscal", f"Sem lançamento em {m:02d}/{a}", c["numero"])
                resumo["alertas"] += 1
        if hoje.day == 1:
            calcular_score(con, c["id"], hoje)
            resumo["scores"] += 1
    return resumo


# ---------------------------------------------------------------- integração com o inventário do órgão
def importar_para_inventario(con: sqlite3.Connection, usuario_id: int, inventario_id: int) -> dict:
    """Lança no inventário do órgão (escopo 3) as emissões COMPROVADAS das terceirizadas no ano.
    Reimportar substitui a importação anterior. Lançamentos sem anexo ficam de fora e são listados."""
    from .mrv import EDITAVEL
    inv = buscar(con, "inventario", inventario_id)
    u = _usuario(con, usuario_id)
    if u["papel"] != "elaborador" or u["organizacao_id"] != inv["organizacao_id"]:
        raise NaoAutorizado("Só um elaborador do órgão importa para o inventário.")
    if inv["status"] not in EDITAVEL:
        raise ErroNegocio(f"Inventário em '{inv['status']}' não aceita alterações.")
    ano = inv["ano"]
    with transacao(con):
        antigos = [dict(r) for r in con.execute(
            "SELECT * FROM atividade WHERE inventario_id=? AND contrato_id IS NOT NULL", (inventario_id,))]
        con.execute("DELETE FROM atividade WHERE inventario_id=? AND contrato_id IS NOT NULL", (inventario_id,))
        criadas, pendentes = [], 0
        for c in con.execute(
                """SELECT c.*, f.nome AS fornecedor FROM contrato c JOIN organizacao f ON f.id=c.fornecedor_id
                   WHERE c.orgao_id=? AND substr(c.vigencia_inicio,1,4) <= ? AND substr(c.vigencia_fim,1,4) >= ?""",
                (inv["organizacao_id"], str(ano), str(ano))).fetchall():
            r = con.execute(
                """SELECT COALESCE(SUM(CASE WHEN anexo_id IS NOT NULL THEN tco2e END),0) t,
                          SUM(anexo_id IS NOT NULL) n, SUM(anexo_id IS NULL) p
                   FROM lancamento_fornecedor WHERE contrato_id=? AND ano=?""", (c["id"], ano)).fetchone()
            pendentes += r["p"] or 0
            if not r["n"]:
                continue
            memoria = (f"Soma de {r['n']} lançamento(s) comprovado(s) de {c['fornecedor']} no contrato "
                       f"{c['numero']} em {ano}" + (f"; {r['p']} pendente(s) de comprovação não incluído(s)"
                                                     if r["p"] else ""))
            aid = con.execute(
                """INSERT INTO atividade (inventario_id, descricao, escopo, natureza, origem, contrato_id,
                   quantidade, tco2e, memoria) VALUES (?,?,3,'emissao',?,?,?,?,?)""",
                (inventario_id, f"Serviços terceirizados — {c['fornecedor']} ({c['objeto']})",
                 f"contrato:{c['id']}", c["id"], r["t"], r["t"], memoria)).lastrowid
            criadas.append(aid)
        registrar(con, usuario_id, "importar_terceirizadas", "inventario", inventario_id,
                  antes=antigos or None, depois={"atividades": criadas, "pendentes_ignorados": pendentes})
    return {"atividades_criadas": len(criadas), "lancamentos_pendentes_ignorados": pendentes}
