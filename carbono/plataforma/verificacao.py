"""Pacote de evidências para a verificação independente do inventário (ABNT NBR ISO 14064-3).

Um arquivo .zip com tudo o que o organismo verificador costuma pedir, para reduzir prazo e custo:

    LEIA-ME.txt                 o que há no pacote e como conferir a integridade
    inventario.json             totais, atividades com memória de cálculo, fatores com versão e fonte,
                                histórico de verificação e aprovação, enquadramento no SBCE
    relatorio.html, inventario.xlsx   os relatórios da plataforma
    comprovantes/…              cada comprovante, com o SHA-256 no nome
    trilha.csv                  a trilha de auditoria do inventário e das atividades
    SHA256SUMS.txt              o hash de cada arquivo (sha256sum -c SHA256SUMS.txt)

A plataforma não verifica: monta o pacote. A declaração de verificação é do organismo acreditado.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime, timezone

from .auditoria import registrar
from .banco import ErroNegocio, NaoAutorizado, buscar


def _pode_baixar(con: sqlite3.Connection, u: sqlite3.Row, inv: sqlite3.Row) -> None:
    from .creditos import verificador_habilitado
    if u["papel"] in ("registrador", "auditor", "moderador"):
        return
    if u["papel"] == "elaborador" and u["organizacao_id"] == inv["organizacao_id"]:
        return
    if u["papel"] == "verificador":
        if u["organizacao_id"] == inv["organizacao_id"]:
            raise NaoAutorizado("Verificador não pode pertencer à organização inventariada.")
        if inv["verificado_por"] == u["id"]:
            return
        if inv["status"] == "submetido" and verificador_habilitado(con, u):
            return
    raise NaoAutorizado("Pacote disponível à organização, ao verificador do inventário e aos papéis de controle.")


def _nome_seguro(nome: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", nome or "arquivo").strip("._") or "arquivo"
    return base[:80]


def pacote(con: sqlite3.Connection, usuario_id: int, inventario_id: int) -> tuple[bytes, str]:
    from . import anexos, relatorio
    from .mrv import historico, totais
    inv = buscar(con, "inventario", inventario_id)
    u = buscar(con, "usuario", usuario_id)
    _pode_baixar(con, u, inv)
    if inv["status"] == "rascunho":
        raise ErroNegocio("O pacote de verificação é gerado depois que o inventário é submetido.")
    org = buscar(con, "organizacao", inv["organizacao_id"])
    dados = relatorio._dados(con, inventario_id)
    atividades = dados["atividades"]

    # comprovantes: das atividades e dos lançamentos das terceirizadas importados para o escopo 3
    ids = {a["anexo_id"] for a in atividades if a["anexo_id"]}
    contratos = {a["contrato_id"] for a in atividades if a["contrato_id"]}
    if contratos:
        marc = ",".join("?" * len(contratos))
        ids |= {r[0] for r in con.execute(
            f"SELECT anexo_id FROM lancamento_fornecedor WHERE contrato_id IN ({marc}) AND ano=? AND anexo_id IS NOT NULL",
            (*contratos, inv["ano"]))}

    arquivos: dict[str, bytes] = {}
    pendencias = []
    for aid in sorted(ids):
        try:
            a, conteudo = anexos.abrir(con, usuario_id, aid)
        except (ErroNegocio, NaoAutorizado, OSError) as exc:
            pendencias.append({"anexo_id": aid, "motivo": str(exc)})
            continue
        arquivos[f"comprovantes/{a['sha256'][:16]}_{_nome_seguro(a['nome_original'])}"] = conteudo

    ativ_ids = [a["id"] for a in atividades]
    marc = ",".join("?" * len(ativ_ids)) or "NULL"
    trilha = con.execute(
        f"""SELECT t.em, t.acao, t.entidade, t.entidade_id, us.nome AS usuario, us.papel, t.ip, t.antes, t.depois
            FROM auditoria t LEFT JOIN usuario us ON us.id = t.usuario_id
            WHERE (t.entidade='inventario' AND t.entidade_id=?) OR (t.entidade='atividade' AND t.entidade_id IN ({marc}))
            ORDER BY t.id""", (inventario_id, *ativ_ids)).fetchall()
    csvb = io.StringIO()
    w = csv.writer(csvb, delimiter=";")
    w.writerow(["em", "acao", "entidade", "entidade_id", "usuario", "papel", "ip", "antes", "depois"])
    for r in trilha:
        w.writerow([r[k] if r[k] is not None else "" for k in r.keys()])

    agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resumo = {"organizacao": {k: org[k] for k in ("nome", "cnpj", "tipo", "esfera")},
              "inventario": {k: inv[k] for k in ("id", "ano", "gwp_conjunto", "status")},
              "metodologia": "GHG Protocol e ABNT NBR ISO 14064-1; só atividades com comprovante entram no total",
              "totais": totais(con, inventario_id), "atividades": atividades, "fatores": dados["fatores"],
              "historico": historico(con, inventario_id), "enquadramento_sbce": dados["enquadramento"],
              "comprovantes_nao_incluidos": pendencias, "gerado_em": agora}
    arquivos["inventario.json"] = json.dumps(resumo, ensure_ascii=False, indent=2, default=str).encode()
    arquivos["relatorio.html"] = relatorio.html_relatorio(con, inventario_id).encode()
    arquivos["inventario.xlsx"] = relatorio.xlsx_relatorio(con, inventario_id)
    arquivos["trilha.csv"] = csvb.getvalue().encode("utf-8-sig")
    arquivos["LEIA-ME.txt"] = (
        f"Pacote de evidências para verificação — {org['nome']}, inventário {inv['ano']}\n"
        f"Gerado em {agora} pela plataforma Lastro Carbono.\n\n"
        "Conteúdo:\n"
        "- inventario.json: totais, atividades com memória de cálculo, fatores (versão e fonte), histórico e "
        "enquadramento no SBCE.\n"
        "- relatorio.html e inventario.xlsx: relatórios do inventário.\n"
        "- comprovantes/: os comprovantes; o nome começa pelo SHA-256 registrado na plataforma.\n"
        "- trilha.csv: trilha de auditoria (quem, quando, IP, antes e depois).\n"
        "- SHA256SUMS.txt: confira a integridade com `sha256sum -c SHA256SUMS.txt`.\n\n"
        f"Comprovantes que não puderam ser incluídos: {len(pendencias)} (detalhes em inventario.json).\n"
        "A plataforma organiza as evidências; a declaração de verificação é do organismo acreditado "
        "(ABNT NBR ISO 14064-3).\n").encode()
    somas = "".join(f"{hashlib.sha256(b).hexdigest()}  {nome}\n" for nome, b in sorted(arquivos.items()))
    arquivos["SHA256SUMS.txt"] = somas.encode()

    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, b in sorted(arquivos.items()):
            z.writestr(nome, b)
    registrar(con, usuario_id, "exportar_pacote_verificacao", "inventario", inventario_id,
              depois={"arquivos": len(arquivos), "comprovantes": len(ids) - len(pendencias),
                      "sha256_pacote": hashlib.sha256(saida.getvalue()).hexdigest()})
    return saida.getvalue(), f"verificacao-{_nome_seguro(org['nome'])}-{inv['ano']}.zip"
