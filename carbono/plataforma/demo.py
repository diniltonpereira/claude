"""Ciclo completo de exemplo com dados fictícios."""
from __future__ import annotations

import os

from . import compensacao, mrv, registro
from .banco import conectar
from .enquadramento import enquadrar


def executar(banco: str = "demo.db") -> dict:
    if os.path.exists(banco):
        os.remove(banco)
    c = conectar(banco)
    ins = lambda sql, *a: c.execute(sql, a).lastrowid  # noqa: E731

    reg = ins("INSERT INTO usuario (nome, papel) VALUES ('Registro estadual', 'registrador')")
    pref = ins("INSERT INTO organizacao (nome,cnpj,tipo,esfera) VALUES "
               "('Prefeitura de Exemplo','11111111000191','orgao_publico','municipal')")
    sanea = ins("INSERT INTO organizacao (nome,cnpj,tipo,contrato_ref,poder_concedente_id) VALUES "
                "('Águas Exemplo S.A.','22222222000191','concessao','Contrato 01/2020',?)", pref)
    flor = ins("INSERT INTO organizacao (nome,cnpj,tipo,contrato_ref) VALUES "
               "('Floresta Viva SPE','33333333000191','concessao_florestal','UMF II — Flona X')")
    ovv = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('OVV Certifica','44444444000191','empresa')")
    conta = {o: registro.abrir_conta(c, o) for o in (pref, sanea, flor)}

    elab = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('Ana (SEMMA)','elaborador',?)", pref)
    verif = ins("INSERT INTO usuario (nome,papel,organizacao_id,ovv_credenciado) VALUES "
                "('Bruno (OVV)','verificador',?,1)", ovv)
    mod = ins("INSERT INTO usuario (nome,papel) VALUES ('Carla (Controladoria)','moderador')")
    gest_flor = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('Davi','elaborador',?)", flor)

    # Aferição: inventário 2025 da prefeitura.
    inv = mrv.criar_inventario(c, elab, pref, 2025)
    for desc, fat, q in [("Frota leve — gasolina C", "gasolina_c", 180_000),
                         ("Frota pesada / coleta de lixo — diesel B", "diesel_b", 420_000),
                         ("Prédios públicos e iluminação — energia", "energia_sin", 9_500),
                         ("Cozinhas escolares — GLP", "glp", 38_000)]:
        mrv.adicionar_atividade(c, elab, inv, descricao=desc, fator=fat, quantidade=q,
                                evidencia="NF/fatura no processo SEI 0001/2026")
    mrv.adicionar_atividade(c, elab, inv, descricao="Recarga de ar-condicionado", gas="HFC-134a",
                            quantidade=0.12, evidencia="Ordem de serviço 77/2025")

    # Moderação: verificação independente + aprovação pela controladoria.
    mrv.transitar(c, elab, inv, "submeter")
    mrv.transitar(c, verif, inv, "assumir")
    mrv.transitar(c, verif, inv, "devolver", "Falta evidência do consumo de GLP de dezembro.")
    mrv.transitar(c, elab, inv, "submeter")
    mrv.transitar(c, verif, inv, "assumir")
    mrv.transitar(c, verif, inv, "verificar", "Conferido por amostragem de 30% das notas.")
    mrv.transitar(c, mod, inv, "aprovar", "Aprovado. Publicar no portal da transparência.")

    # Concessão florestal gera CRVE; prefeitura adquire parte (após contratação regular).
    lote = registro.emitir(c, reg, conta_id=conta[flor], tipo_ativo="CRVE", projeto="REDD+ UMF II",
                           metodologia="Metodologia credenciada SBCE (exemplo)", safra=2024,
                           quantidade=5_000, serial="BR-CRVE-2024-000001")["lote_id"]
    t = mrv.totais(c, inv)
    necessario = int(-(-(t["escopo1"] + t["escopo2"]) // 1))
    registro.transferir(c, gest_flor, lote_id=lote, quantidade=necessario, para_conta=conta[pref],
                        chave_idempotencia="contrato-12-2026-parcela-1")

    simulacao = compensacao.compensar(c, elab, inv, simular=True)
    resultado = compensacao.compensar(c, elab, inv, chave_idempotencia="comp-2025")

    return {
        "totais_inventario_2025": t,
        "enquadramento_prefeitura": enquadrar("orgao_publico", t["escopo1"]).como_dict()["faixa_sbce"],
        "enquadramento_concessao_florestal": [
            o.titulo for o in enquadrar("concessao_florestal", 0, gera_creditos=True,
                                        terras_publicas_ou_tradicionais=True).obrigacoes],
        "historico_mrv": [(e["de_status"], e["para_status"]) for e in mrv.historico(c, inv)],
        "plano_compensacao": simulacao["plano"],
        "aposentado": sum(p["quantidade"] for p in resultado["plano"]),
        "saldo_prefeitura": registro.saldo(c, conta[pref]),
        "saldo_concessao_florestal": sum(l["quantidade"] for l in registro.saldo(c, conta[flor])),
        "auditoria": registro.verificar_cadeia(c),
        "concessionaria_saneamento_id": sanea,
    }
