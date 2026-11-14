"""Ciclo completo de exemplo com dados FICTÍCIOS (nomes, CNPJs e números inventados).

Cenário: uma prefeitura do Norte de Minas com secretarias e prédios, inventários de dois anos,
meta de redução, uma terceirizada de limpeza com lançamentos mensais, score e selo, créditos
CRVE e voluntários certificados, compensação com cancelamento e uma concessionária de
saneamento enquadrada por instalação no SBCE.
"""
from __future__ import annotations

import os
from datetime import date

from . import anexos, compensacao, mrv, painel, registro, terceirizadas
from .banco import conectar
from .enquadramento import checar_projeto_publico, enquadrar_operador, fase_em


def _pdf(c, uid: int, nome: str) -> int:
    """Anexo fictício (um PDF mínimo) para a demonstração."""
    conteudo = f"%PDF-1.4\n% Documento fictício de demonstração: {nome}\n%%EOF\n".encode()
    return anexos.salvar(c, uid, f"{nome}.pdf", "application/pdf", conteudo)["id"]


def executar(banco: str = "demo.db", hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    ano, ano_ant = hoje.year - 1, hoje.year - 2
    for sufixo in ("", "-wal", "-shm"):
        if os.path.exists(banco + sufixo):
            os.remove(banco + sufixo)
    c = conectar(banco)
    ins = lambda sql, *a: c.execute(sql, a).lastrowid  # noqa: E731

    # ------------------------------------------------ cadastros
    reg = ins("INSERT INTO usuario (nome, papel) VALUES ('Equipe CarbonMG', 'registrador')")
    pref = ins("INSERT INTO organizacao (nome,cnpj,tipo,esfera,municipio_ibge) VALUES "
               "('Prefeitura de Vale Verde (fictícia)','11111111000191','orgao_publico','municipal','3100000')")
    sanea = ins("INSERT INTO organizacao (nome,cnpj,tipo,contrato_ref,poder_concedente_id) VALUES "
                "('Águas Vale Verde S.A.','22222222000191','concessao','Contrato 01/2020',?)", pref)
    flor = ins("INSERT INTO organizacao (nome,cnpj,tipo,contrato_ref) VALUES "
               "('Floresta Viva SPE','33333333000191','concessao_florestal','UMF II')")
    oia = ins("INSERT INTO organizacao (nome,cnpj,tipo) VALUES ('Inspeção Acreditada Ltda.','44444444000191','empresa')")
    conta = {o: registro.abrir_conta(c, o) for o in (pref, sanea, flor)}

    elab = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('Ana (Meio Ambiente)','elaborador',?)", pref)
    verif = ins("INSERT INTO usuario (nome,papel,organizacao_id,acreditado) VALUES "
                "('Bruno (organismo acreditado)','verificador',?,1)", oia)
    mod = ins("INSERT INTO usuario (nome,papel) VALUES ('Carla (Controladoria)','moderador')")
    gest_flor = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('Davi','elaborador',?)", flor)
    elab_san = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('Eva (Águas)','elaborador',?)", sanea)
    fiscal = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('Fábio (fiscal)','fiscal',?)", pref)
    auditor = ins("INSERT INTO usuario (nome,papel) VALUES ('Gil (auditor TCE)','auditor')")

    # ------------------------------------------------ hierarquia da prefeitura
    def unid(nome, natureza="unidade", pai=None):
        return mrv.criar_instalacao(c, elab, pref, nome=nome, natureza=natureza, pai_id=pai)
    saude = unid("Secretaria de Saúde")
    educ = unid("Secretaria de Educação")
    obras = unid("Secretaria de Obras")
    hospital = unid("Hospital Municipal", "instalacao", saude)
    escolas = unid("Escolas municipais", "instalacao", educ)
    frota = unid("Frota de obras e coleta", "fonte_movel", obras)

    # ------------------------------------------------ inventários (ano anterior e ano de referência)
    def inventario(a: int, fator_frota: float) -> int:
        inv = mrv.criar_inventario(c, elab, pref, a)
        for mes in range(1, 13):
            mrv.adicionar_atividade(c, elab, inv, descricao=f"Diesel B — frota ({mes:02d}/{a})", fator="diesel_b",
                                    quantidade=35_000 * fator_frota, mes=mes, instalacao_id=frota,
                                    anexo_id=_pdf(c, elab, f"NF diesel {mes:02d}-{a}"))
            mrv.adicionar_atividade(c, elab, inv, descricao=f"Energia — hospital ({mes:02d}/{a})",
                                    fator="energia_sin_kwh", quantidade=60_000, mes=mes, instalacao_id=hospital,
                                    anexo_id=_pdf(c, elab, f"Fatura energia {mes:02d}-{a}"))
        mrv.adicionar_atividade(c, elab, inv, descricao="GLP — cozinhas escolares", fator="glp", quantidade=38_000,
                                instalacao_id=escolas, anexo_id=_pdf(c, elab, f"NF GLP {a}"))
        mrv.adicionar_atividade(c, elab, inv, descricao="Recarga de ar-condicionado", gas="HFC-134a",
                                quantidade=0.12, instalacao_id=hospital, anexo_id=_pdf(c, elab, f"OS ar {a}"))
        return inv

    inv_ant = inventario(ano_ant, 1.30)
    inv = inventario(ano, 1.0)
    mrv.adicionar_atividade(c, elab, inv, descricao="Usina solar do hospital (geração)", fator="energia_sin_kwh",
                            quantidade=120_000, evitada=True, instalacao_id=hospital,
                            anexo_id=_pdf(c, elab, "Relatório de geração solar"))
    mrv.adicionar_atividade(c, elab, inv, descricao="Restauração do Parque Municipal", gas="CO2", quantidade=40,
                            remocao=True, anexo_id=_pdf(c, elab, "Laudo de biomassa"))
    mrv.adicionar_atividade(c, elab, inv, descricao="Gasolina — veículos da Educação (sem nota)", fator="gasolina_c",
                            quantidade=5_000, instalacao_id=escolas)  # fica pendente de comprovação
    painel.definir_meta(c, elab, pref, ano_base=ano_ant, ano_alvo=ano, reducao_percentual=10)

    # ------------------------------------------------ terceirizada de limpeza
    cid = terceirizadas.convidar(c, fiscal, orgao_id=pref, fornecedor_cnpj="55555555000191",
                                 fornecedor_nome="Limpa Bem Serviços Ltda. (fictícia)", numero="045/2023",
                                 objeto="Limpeza e conservação predial", vigencia_inicio=f"{ano_ant}-01-01",
                                 vigencia_fim=f"{hoje.year + 1}-12-31", clausula_relato=True)
    forn_org = c.execute("SELECT fornecedor_id FROM contrato WHERE id=?", (cid,)).fetchone()[0]
    forn = ins("INSERT INTO usuario (nome,papel,organizacao_id) VALUES ('Helena (Limpa Bem)','fornecedor',?)", forn_org)
    terceirizadas.aceitar(c, forn, cid)
    a, m, n = ano_ant, 1, 0
    while (a, m) < (hoje.year, hoje.month):
        quem = forn if n % 7 else fiscal  # de vez em quando o fiscal lança pela terceirizada
        terceirizadas.lancar(c, quem, cid, ano=a, mes=m, descricao="Diesel — veículos de apoio", fator="diesel_b",
                             quantidade=1_400 - 15 * n, anexo_id=_pdf(c, quem, f"NF limpeza {m:02d}-{a}"), hoje=hoje)
        a, m, n = (a + 1, 1, n + 1) if m == 12 else (a, m + 1, n + 1)
    for i, desc in enumerate(("Treinamento de condução econômica", "Coleta seletiva nos prédios atendidos")):
        terceirizadas.registrar_acao(c, forn, cid, data=f"{hoje.year}-0{i + 3}-15", descricao=desc,
                                     anexo_id=_pdf(c, forn, desc))
    terceirizadas.importar_para_inventario(c, elab, inv)
    terceirizadas.importar_para_inventario(c, elab, inv_ant)

    # ------------------------------------------------ verificação e aprovação dos dois inventários
    for i in (inv_ant, inv):
        mrv.transitar(c, elab, i, "submeter")
        mrv.transitar(c, verif, i, "assumir")
        if i == inv:
            mrv.transitar(c, verif, i, "devolver", "Anexar a nota do GLP de dezembro.")
            mrv.transitar(c, elab, i, "submeter")
            mrv.transitar(c, verif, i, "assumir")
        mrv.transitar(c, verif, i, "verificar", "Conferido por amostragem de 30% das notas.")
        mrv.transitar(c, mod, i, "aprovar", "Aprovado. Publicar no portal da transparência.")

    # ------------------------------------------------ créditos e compensação
    crve = registro.emitir(c, reg, conta_id=conta[flor], tipo_ativo="CRVE", projeto="REDD+ UMF II",
                           metodologia="Metodologia credenciada SBCE (exemplo)", safra=ano_ant,
                           quantidade=5_000, serial=f"BR-CRVE-{ano_ant}-000001")["lote_id"]
    registro.emitir(c, reg, conta_id=conta[pref], tipo_ativo="VOLUNTARIO", projeto="Biogás Aterro Regional (fictício)",
                    metodologia="ACM0001", certificadora="Verra", certificado_anexo_id=_pdf(c, reg, "Certificado VCU"),
                    safra=ano, quantidade=300, serial=f"VCS-0000-{ano}-000300")
    registro.transferir(c, gest_flor, lote_id=crve, quantidade=1_000, para_conta=conta[pref],
                        chave_idempotencia="contrato-12-parcela-1")
    simulacao = compensacao.compensar(c, elab, inv, simular=True)
    refs = {p["lote_id"]: "VCS-RET-0000-000123 (fictício)" for p in simulacao["plano"] if p["exige_registro_externo"]}
    resultado = compensacao.compensar(c, elab, inv, registros_externos=refs, chave_idempotencia="comp-ano")
    selo = terceirizadas.emitir_selo(c, fiscal, cid, hoje)
    terceirizadas.rotina_diaria(c, hoje)

    # ------------------------------------------------ concessionária: enquadramento por instalação (art. 30)
    inv_s = mrv.criar_inventario(c, elab_san, sanea, ano)
    for nome, gas, q, kw in [("Aterro sanitário Leste", "CH4", 1_150, {}),
                             ("Aterro Oeste (captação e queima)", "CH4", 3_000, {"residuos_neutralizacao": True}),
                             ("Frota operacional", "CO2", 12_000, {"natureza": "fonte_movel"}),
                             ("ETE Centro", "N2O", 30, {})]:
        iid = mrv.criar_instalacao(c, elab_san, sanea, nome=nome, **kw)
        mrv.adicionar_atividade(c, elab_san, inv_s, descricao=nome, gas=gas, quantidade=q, instalacao_id=iid,
                                anexo_id=_pdf(c, elab_san, f"Plano de monitoramento {nome}"))
    for usuario, acao in [(elab_san, "submeter"), (verif, "assumir"), (verif, "verificar"), (mod, "aprovar")]:
        mrv.transitar(c, usuario, inv_s, acao)
    op = enquadrar_operador("concessao", mrv.totais_por_instalacao(c, inv_s))
    registro.emitir(c, reg, conta_id=conta[sanea], tipo_ativo="CBE", projeto="Alocação PNA (exemplo)",
                    metodologia="PNA", safra=ano, quantidade=30_000, serial=f"BR-CBE-{ano}-000001")
    concil = compensacao.compensar(c, elab_san, inv_s, finalidade="conciliacao_sbce", simular=True)

    p = painel.painel(c, pref, ano)
    return {
        "fase_sbce_hoje": fase_em(hoje).numero,
        "prefeitura": {
            "totais": p["totais"], "variacao_anual_percentual": p["variacao_anual_percentual"],
            "meta": p["meta"], "alertas": p["alertas"], "percentual_compensado": p["percentual_compensado"],
            "cancelado": sum(x["quantidade"] for x in resultado["plano"]),
            "lotes_voluntarios_com_registro_externo": len(refs),
        },
        "terceirizada": {"selo": f"Nível {selo['classe']} ({selo['score']:g} pontos)", "codigo_selo": selo["codigo"]},
        "projeto_publico_sem_licitacao": checar_projeto_publico(
            esfera="municipal", proprietario_e_usufrutuario=True, sobreposicao_terceiros=False,
            parceria_privada=True, licitacao=False, anuencia_gestor_area=True),
        "concessionaria": {"faixa": op["faixa_sbce"], "base_conciliacao": op["base_conciliacao_tco2e"],
                           "deficit_de_cbe": concil["deficit"]},
        "livro_razao": registro.verificar_cadeia(c),
        "usuarios": {"registrador": reg, "gestora_prefeitura": elab, "verificador": verif, "moderadora": mod,
                     "concessao_florestal": gest_flor, "concessionaria": elab_san, "fiscal": fiscal,
                     "auditor": auditor, "terceirizada": forn},
    }
