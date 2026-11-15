"""Conferência de créditos voluntários e credenciamento de verificadores.

Os registros da Verra e da Gold Standard não oferecem, hoje, consulta automática aberta (a Verra exige
conta aprovada para a API; a Gold Standard bloqueia clientes automáticos). Por isso a conferência é
documental e assistida:

1. Na escrituração, o lote voluntário traz o link do registro e o bloco de séries. A plataforma confere
   o endereço (domínio da certificadora), a quantidade e a safra lidas da série, e recusa bloco repetido
   ou intervalo sobreposto do mesmo projeto: o mesmo crédito não entra duas vezes.
2. Antes de atestar a liquidação de um pedido de crédito voluntário, o fiscal confere o lote no registro
   público e registra o resultado com evidência (PDF ou imagem da página). Sem conferência "conforme",
   a liquidação fica bloqueada.

Verificadores: credenciamento aberto com prova de acreditação e validade, analisado pela administração.
Uma organização com credenciamento passa a depender dele (vencido = não assume inventários).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from urllib.parse import urlsplit

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, buscar

DOMINIOS = {"Verra": "verra.org", "Gold Standard": "goldstandard.org"}

# Verra (VCU): <lote>-<início>-<fim>-VCS-VCU-<…>-VER-<país>-<escopo>-<projeto>-<ddmmaaaa>-<ddmmaaaa>-<0|1>
_VERRA = re.compile(r"^\d+-(\d+)-(\d+)-VCS-VCU-[\w]+-VER-[A-Z]{2}-\d+-(\d+)-\d{4}(\d{4})-\d{8}-[01]$")
# Gold Standard: GS<n>-<n>-<país>-GS<projeto>-<n>-<safra>-<n>-<início>-<fim>
_GOLD = re.compile(r"^GS\d+-\d+-[A-Z]{2}-(GS\d+)-\d+-(\d{4})-\d+-(\d+)-(\d+)$")


def serie_verra(inicio: int, quantidade: int, safra: int, projeto: int, lote: int = 1000) -> tuple[str, str]:
    """(link, bloco) no formato da Verra. Usado na demonstração e nos testes, com dados fictícios."""
    bloco = f"{lote}-{inicio}-{inicio + quantidade - 1}-VCS-VCU-259-VER-BR-1-{projeto}-0101{safra}-3112{safra}-0"
    return f"https://registry.verra.org/app/projectDetail/VCS/{projeto}", bloco


def interpretar_serie(certificadora: str, bloco: str) -> dict | None:
    """Lê projeto, safra e intervalo da série nos formatos conhecidos; None se o formato não for reconhecido."""
    b = bloco.strip()
    if certificadora == "Verra" and (m := _VERRA.match(b)):
        return {"projeto_ref": m.group(3), "safra": int(m.group(4)), "inicio": int(m.group(1)), "fim": int(m.group(2))}
    if certificadora == "Gold Standard" and (m := _GOLD.match(b)):
        return {"projeto_ref": m.group(1), "safra": int(m.group(2)), "inicio": int(m.group(3)), "fim": int(m.group(4))}
    return None


def validar_bloco(con: sqlite3.Connection, *, certificadora: str, registro_url: str, serie_bloco: str,
                  quantidade: int, safra: int) -> dict:
    """Confere o bloco antes da escrituração. Devolve os campos para `lote_registro`."""
    url = (registro_url or "").strip()
    partes = urlsplit(url)
    dominio = DOMINIOS.get(certificadora)
    host = (partes.hostname or "").lower()
    if partes.scheme != "https" or not dominio or not (host == dominio or host.endswith("." + dominio)):
        raise ErroNegocio(f"Informe o link https do lote no registro da {certificadora} (domínio {dominio}).")
    bloco = " ".join((serie_bloco or "").split())
    if len(bloco) < 6:
        raise ErroNegocio("Informe o bloco de números de série exatamente como aparece no registro.")
    dados = {"registro_url": url, "serie_bloco": bloco, "projeto_ref": None, "serie_inicio": None, "serie_fim": None}
    serie = interpretar_serie(certificadora, bloco)
    if serie:
        tamanho = serie["fim"] - serie["inicio"] + 1
        if tamanho <= 0:
            raise ErroNegocio("O bloco de séries tem início maior que o fim.")
        if tamanho != quantidade:
            raise ErroNegocio(f"O bloco de séries tem {tamanho} unidades, mas o lote informa {quantidade}.")
        if serie["safra"] != safra:
            raise ErroNegocio(f"A série indica safra {serie['safra']}, mas o lote informa {safra}.")
        sobreposto = con.execute(
            """SELECT lote_id FROM lote_registro WHERE certificadora=? AND projeto_ref=?
               AND serie_inicio <= ? AND serie_fim >= ?""",
            (certificadora, serie["projeto_ref"], serie["fim"], serie["inicio"])).fetchone()
        if sobreposto:
            raise ErroNegocio(f"Parte destas séries já foi escriturada no lote nº {sobreposto['lote_id']}. "
                              "O mesmo crédito não pode entrar duas vezes.")
        dados.update(projeto_ref=serie["projeto_ref"], serie_inicio=serie["inicio"], serie_fim=serie["fim"])
    repetido = con.execute("SELECT lote_id FROM lote_registro WHERE certificadora=? AND serie_bloco=?",
                           (certificadora, bloco)).fetchone()
    if repetido:
        raise ErroNegocio(f"Este bloco de séries já foi escriturado no lote nº {repetido['lote_id']}.")
    return dados


def gravar_bloco(con: sqlite3.Connection, lote_id: int, certificadora: str, dados: dict) -> None:
    con.execute("""INSERT INTO lote_registro (lote_id, certificadora, registro_url, serie_bloco, projeto_ref,
                   serie_inicio, serie_fim) VALUES (?,?,?,?,?,?,?)""",
                (lote_id, certificadora, dados["registro_url"], dados["serie_bloco"], dados["projeto_ref"],
                 dados["serie_inicio"], dados["serie_fim"]))


def lote_raiz(con: sqlite3.Connection, lote_id: int) -> sqlite3.Row:
    """O lote escriturado de onde um lote transferido saiu (transferências criam lotes-filhos)."""
    lote = buscar(con, "lote", lote_id)
    while lote["lote_origem_id"]:
        lote = buscar(con, "lote", lote["lote_origem_id"])
    return lote


def bloco_do_lote(con: sqlite3.Connection, lote_id: int) -> dict | None:
    r = con.execute("SELECT * FROM lote_registro WHERE lote_id=?", (lote_raiz(con, lote_id)["id"],)).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------- conferência pelo fiscal
def conferir(con: sqlite3.Connection, usuario_id: int, pedido_id: int, *, resultado: str,
             evidencia_anexo_id: int, observacao: str | None = None) -> int:
    from .anexos import exigir_acesso
    p = buscar(con, "pedido", pedido_id)
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "fiscal" or u["organizacao_id"] != p["orgao_id"]:
        raise NaoAutorizado("Só o fiscal do órgão comprador confere os créditos no registro.")
    if p["status"] != "entregue" or not p["lote_entregue_id"]:
        raise ErroNegocio("A conferência no registro é feita depois da entrega dos créditos.")
    if resultado not in ("conforme", "divergente"):
        raise ErroNegocio("Resultado deve ser 'conforme' ou 'divergente'.")
    if resultado == "divergente" and not (observacao or "").strip():
        raise ErroNegocio("Descreva a divergência encontrada no registro.")
    exigir_acesso(con, usuario_id, evidencia_anexo_id)
    raiz = lote_raiz(con, p["lote_entregue_id"])
    cid = con.execute("""INSERT INTO conferencia_registro (pedido_id, lote_id, usuario_id, resultado, observacao,
                         evidencia_anexo_id) VALUES (?,?,?,?,?,?)""",
                      (pedido_id, raiz["id"], usuario_id, resultado, (observacao or "").strip() or None,
                       evidencia_anexo_id)).lastrowid
    registrar(con, usuario_id, "conferir_registro", "pedido", pedido_id,
              depois={"resultado": resultado, "lote": raiz["serial"], "evidencia_anexo_id": evidencia_anexo_id})
    return cid


def ultima_conferencia(con: sqlite3.Connection, pedido_id: int) -> dict | None:
    r = con.execute("SELECT * FROM conferencia_registro WHERE pedido_id=? ORDER BY id DESC LIMIT 1",
                    (pedido_id,)).fetchone()
    return dict(r) if r else None


def exigir_conferencia(con: sqlite3.Connection, pedido_id: int) -> None:
    """Chamada na liquidação: crédito voluntário só liquida com conferência 'conforme' no registro."""
    p = buscar(con, "pedido", pedido_id)
    lote = buscar(con, "lote", p["lote_entregue_id"])
    if lote["tipo_ativo"] != "VOLUNTARIO":
        return
    c = ultima_conferencia(con, pedido_id)
    if not c:
        raise ErroNegocio("Antes de liquidar, confira o lote no registro da certificadora e registre o resultado "
                          "com a evidência.")
    if c["resultado"] != "conforme":
        raise ErroNegocio("A última conferência no registro encontrou divergência: liquidação bloqueada.")


# ---------------------------------------------------------------- credenciamento de verificadores
def solicitar_acreditacao(con: sqlite3.Connection, usuario_id: int, *, acreditador: str, numero: str, normas: str,
                          valido_ate: str, documento_anexo_id: int, escopo: str | None = None) -> int:
    from .anexos import exigir_acesso
    u = buscar(con, "usuario", usuario_id)
    if u["papel"] != "verificador" or not u["organizacao_id"]:
        raise NaoAutorizado("Só o verificador do organismo solicita o credenciamento.")
    if date.fromisoformat(valido_ate) < date.today():
        raise ErroNegocio("A acreditação informada já está vencida.")
    if not numero.strip() or not acreditador.strip() or not normas.strip():
        raise ErroNegocio("Informe o acreditador, o nº da acreditação e as normas.")
    exigir_acesso(con, usuario_id, documento_anexo_id)
    aid = con.execute("""INSERT INTO acreditacao_verificador (organizacao_id, acreditador, numero, normas, escopo,
                         valido_ate, documento_anexo_id, solicitado_por) VALUES (?,?,?,?,?,?,?,?)""",
                      (u["organizacao_id"], acreditador.strip(), numero.strip(), normas.strip(),
                       (escopo or "").strip() or None, valido_ate, documento_anexo_id, usuario_id)).lastrowid
    registrar(con, usuario_id, "solicitar_acreditacao", "acreditacao_verificador", aid,
              depois={"numero": numero, "valido_ate": valido_ate})
    return aid


def analisar_acreditacao(con: sqlite3.Connection, usuario_id: int, acreditacao_id: int, *, aprovar: bool,
                         parecer: str) -> None:
    """A administração confere a acreditação na fonte (ex.: consulta pública do Inmetro) e decide."""
    if buscar(con, "usuario", usuario_id)["papel"] != "registrador":
        raise NaoAutorizado("Só a administração analisa credenciamentos.")
    a = buscar(con, "acreditacao_verificador", acreditacao_id)
    if a["status"] != "em_analise":
        raise ErroNegocio("Este pedido já foi analisado.")
    if not parecer.strip():
        raise ErroNegocio("Registre o parecer, com a fonte consultada.")
    con.execute("""UPDATE acreditacao_verificador SET status=?, parecer=?, analisado_por=?,
                   analisado_em=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
                ("aprovada" if aprovar else "indeferida", parecer.strip(), usuario_id, acreditacao_id))
    registrar(con, usuario_id, "analisar_acreditacao", "acreditacao_verificador", acreditacao_id,
              depois={"status": "aprovada" if aprovar else "indeferida"})


def acreditacoes(con: sqlite3.Connection, usuario_id: int) -> list[dict]:
    u = buscar(con, "usuario", usuario_id)
    sql = """SELECT a.*, o.nome AS organismo FROM acreditacao_verificador a
             JOIN organizacao o ON o.id = a.organizacao_id"""
    if u["papel"] in ("registrador", "auditor", "moderador", "elaborador"):
        return [dict(r) for r in con.execute(sql + " ORDER BY a.id DESC")]
    return [dict(r) for r in con.execute(sql + " WHERE a.organizacao_id=? ORDER BY a.id DESC",
                                         (u["organizacao_id"],))]


def verificador_habilitado(con: sqlite3.Connection, u: sqlite3.Row, hoje: date | None = None) -> bool:
    """Com credenciamento registrado, vale a última acreditação aprovada e vigente; sem nenhum, o cadastro
    manual (marcação de acreditado feita pela administração), como antes."""
    hoje = hoje or date.today()
    if not u["organizacao_id"]:
        return bool(u["acreditado"])
    regs = con.execute("SELECT status, valido_ate FROM acreditacao_verificador WHERE organizacao_id=?",
                       (u["organizacao_id"],)).fetchall()
    if not regs:
        return bool(u["acreditado"])
    return any(r["status"] == "aprovada" and date.fromisoformat(r["valido_ate"]) >= hoje for r in regs)
