"""Enquadramento legal: SBCE (Lei 15.042/2024) e regime do contratante público.

Os artigos citados foram conferidos no texto integral da Lei 15.042/2024. O que
depende de regulamento do órgão gestor (patamares majorados, percentual de CRVE,
calendário efetivo) fica parametrizado. Normas de outras leis que não puderam ser
conferidas nesta versão levam `conferir=True`. Não substitui parecer jurídico.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

# Art. 30, I e II. Podem ser MAJORADOS por ato do órgão gestor (art. 30, § 1º).
LIMIAR_MONITORAMENTO = 10_000   # art. 29, I, II e IV: plano de monitoramento e relato
LIMIAR_CONCILIACAO = 25_000     # art. 29, I a IV: inclui conciliação periódica (art. 34)

VIGENCIA_LEI = date(2024, 12, 12)  # art. 58 (publicação no DOU de 12/12/2024)

TIPOS_ORGANIZACAO = ("orgao_publico", "ppp", "concessao", "concessao_florestal", "empresa")
ESFERAS = ("federal", "estadual", "municipal")


@dataclass
class Obrigacao:
    titulo: str
    fundamento: str
    detalhe: str
    exigivel: bool = True
    conferir: bool = False


@dataclass
class Fase:
    numero: str
    descricao: str
    inicio: date
    fim: date | None


@dataclass
class Enquadramento:
    emissoes_escopo1_tco2e: float
    faixa_sbce: str
    fase_sbce: str
    obrigacoes: list[Obrigacao] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Fases de implementação (art. 50)
# ---------------------------------------------------------------------------
def _somar_anos(d: date, anos: int) -> date:
    try:
        return d.replace(year=d.year + anos)
    except ValueError:  # 29/02
        return d.replace(year=d.year + anos, day=28)


def calendario(fase1_prorrogada: bool = True, duracao_pna_anos: int | None = None) -> list[Fase]:
    """Fases do art. 50. A fase I tem 12 meses, prorrogáveis por mais 12 (art. 50, I).

    Por padrão assume a prorrogação (regulamentação até dez./2026). Confira o ato que a
    formalizou. A duração do 1º Plano Nacional de Alocação será fixada pelo próprio PNA.
    """
    fim1 = _somar_anos(VIGENCIA_LEI, 2 if fase1_prorrogada else 1)
    fim2 = _somar_anos(fim1, 1)    # art. 50, II
    fim3 = _somar_anos(fim2, 2)    # art. 50, III
    fim4 = _somar_anos(fim3, duracao_pna_anos) if duracao_pna_anos else None
    return [
        Fase("I", "Regulamentação da lei", VIGENCIA_LEI, fim1),
        Fase("II", "Operacionalização, pelos operadores, dos instrumentos de relato", fim1, fim2),
        Fase("III", "Somente plano de monitoramento e relato de emissões e remoções", fim2, fim3),
        Fase("IV", "1º Plano Nacional de Alocação: CBE gratuita e mercado de ativos", fim3, fim4),
        Fase("V", "Implementação plena (fim do 1º PNA)", fim4 or fim3, None),
    ]


def fase_em(data: date, fase1_prorrogada: bool = True, duracao_pna_anos: int | None = None) -> Fase:
    fases = calendario(fase1_prorrogada, duracao_pna_anos)
    if data < VIGENCIA_LEI:
        raise ValueError("Data anterior à vigência da Lei 15.042/2024.")
    for f in fases[:4]:
        if f.fim is None or data < f.fim:
            return f
    return fases[4]


# ---------------------------------------------------------------------------
# Faixa e obrigações
# ---------------------------------------------------------------------------
def faixa_sbce(emissoes_escopo1: float, limiar_monitoramento: float = LIMIAR_MONITORAMENTO,
               limiar_conciliacao: float = LIMIAR_CONCILIACAO) -> str:
    if emissoes_escopo1 > limiar_conciliacao:
        return "regulado_conciliacao"
    if emissoes_escopo1 > limiar_monitoramento:
        return "regulado_monitoramento"
    return "nao_regulado"


def enquadrar(tipo: str, emissoes_escopo1: float, *, data_referencia: date | None = None,
              esfera: str | None = None,
              atividade_agropecuaria_primaria: bool = False,
              residuos_com_neutralizacao: bool = False,
              gera_creditos: bool = False,
              terras_publicas_ou_tradicionais: bool = False,
              limiar_monitoramento: float = LIMIAR_MONITORAMENTO,
              limiar_conciliacao: float = LIMIAR_CONCILIACAO,
              fase1_prorrogada: bool = True, por_instalacao: bool = False) -> Enquadramento:
    if tipo not in TIPOS_ORGANIZACAO:
        raise ValueError(f"Tipo de organização inválido: {tipo!r}")
    if esfera is not None and esfera not in ESFERAS:
        raise ValueError(f"Esfera inválida: {esfera!r}")
    if emissoes_escopo1 < 0:
        raise ValueError("Emissões não podem ser negativas.")
    if limiar_monitoramento < LIMIAR_MONITORAMENTO or limiar_conciliacao < LIMIAR_CONCILIACAO:
        raise ValueError("Os patamares só podem ser majorados, nunca reduzidos (art. 30, § 1º).")

    fase = fase_em(data_referencia or date.today(), fase1_prorrogada)
    excluido = atividade_agropecuaria_primaria or residuos_com_neutralizacao
    faixa = "nao_regulado" if excluido else faixa_sbce(emissoes_escopo1, limiar_monitoramento,
                                                       limiar_conciliacao)
    e = Enquadramento(round(emissoes_escopo1, 3), faixa, f"Fase {fase.numero}: {fase.descricao}")
    ob = e.obrigacoes.append
    relato_exigivel = fase.numero in ("III", "IV", "V")
    conciliacao_exigivel = fase.numero in ("IV", "V")

    # --- SBCE ---------------------------------------------------------------
    if atividade_agropecuaria_primaria:
        e.alertas.append("Produção primária agropecuária não é atividade regulada e não se sujeita "
                         "a obrigações do SBCE (art. 1º, § 2º), mas pode gerar créditos.")
    if residuos_com_neutralizacao:
        e.alertas.append("Unidade de tratamento/destinação de resíduos ou efluentes que comprove "
                         "sistemas de neutralização fica fora dos patamares (art. 30, § 3º). "
                         "Guarde a comprovação técnica.")
    if faixa != "nao_regulado":
        ob(Obrigacao("Plano de monitoramento", "Lei 15.042/2024, arts. 29, I, 30 e 31",
                     "Submeter ao órgão gestor, para aprovação prévia, a cada período de compromisso.",
                     exigivel=relato_exigivel))
        ob(Obrigacao("Relato anual de emissões e remoções", "Lei 15.042/2024, arts. 29, II, 32 e 33",
                     "Relato anual conforme o plano aprovado, avaliado por organismo de inspeção "
                     "acreditado. Os dados vão para a conta do operador no Registro Central.",
                     exigivel=relato_exigivel))
        ob(Obrigacao("Regime sancionador", "Lei 15.042/2024, arts. 35 a 41",
                     "Advertência; multa de valor não inferior ao custo das obrigações descumpridas, "
                     "até 3% do faturamento bruto do grupo no ano anterior (até 4% na reincidência); "
                     "embargo; suspensão; restritivas de direitos, inclusive proibição de contratar "
                     "com a administração pública por até 3 anos (art. 37, VI, e). Infrações leves "
                     "podem ser sanadas por notificação prévia (arts. 40 e 41)."))
    if faixa == "regulado_conciliacao":
        ob(Obrigacao("Conciliação periódica de obrigações", "Lei 15.042/2024, arts. 29, III, 34 e 2º, V",
                     "Ao fim de cada período de compromisso, deter CBE e CRVE (limitada ao percentual "
                     "máximo do PNA, arts. 12, I, e 21, IV) em quantidade igual às emissões líquidas; "
                     "relato anual de conciliação.", exigivel=conciliacao_exigivel))
    if faixa == "nao_regulado" and not excluido and emissoes_escopo1 > limiar_monitoramento * 0.8:
        e.alertas.append("Emissões acima de 80% do patamar de monitoramento: acompanhar a tendência.")
    if faixa != "nao_regulado" and not relato_exigivel:
        e.alertas.append(f"Na fase {fase.numero} as obrigações ainda não são exigíveis. Use o período "
                         "para estruturar o plano de monitoramento.")
    if not por_instalacao:
        e.alertas.append("O patamar se aplica por instalação ou fonte do operador (art. 30). Este "
                         "enquadramento usa um único valor; use enquadrar_operador().")

    # --- Contratante público -----------------------------------------------
    if tipo == "orgao_publico":
        ob(Obrigacao("Política Nacional sobre Mudança do Clima", "Lei 12.187/2009, art. 4º, VIII "
                     "(redação da Lei 15.042/2024, art. 52)",
                     "Estímulo ao desenvolvimento do SBCE como objetivo da PNMC."))
        ob(Obrigacao("Contratações sustentáveis", "Lei 14.133/2021, arts. 5º e 11, IV",
                     "Desenvolvimento nacional sustentável como princípio e objetivo da licitação; "
                     "critérios ambientais e ciclo de vida no ETP e no termo de referência."))
        ob(Obrigacao("Compensação voluntária pelo ente", "Lei 15.042/2024, art. 45; CF, art. 37; "
                     "Lei 14.133/2021; Lei 4.320/1964",
                     "A compensação exige o cancelamento do ativo no Registro Central. A compra pede "
                     "dotação, motivação e contratação regular (ou dispensa fundamentada)."))
        if esfera in ("estadual", "municipal"):
            ob(Obrigacao("Vedação de tributo sobre emissões reguladas", "Lei 15.042/2024, art. 22",
                         "A fixação de limites de emissão é competência exclusiva da União. São "
                         "vedadas a dupla regulação e qualquer tributação sobre emissões de "
                         "atividades, instalações ou fontes reguladas pelo SBCE."))
    if tipo == "ppp":
        ob(Obrigacao("Repartição de riscos", "Lei 11.079/2004, art. 5º, III",
                     "Alocar no contrato o risco regulatório-climático (custo de CBE, art. 11, § 1º, "
                     "II) e a titularidade e a repartição de eventuais créditos (art. 43, caput)."))
    if tipo in ("concessao", "ppp", "concessao_florestal"):
        ob(Obrigacao("Identificar o operador regulado", "Lei 15.042/2024, art. 2º, XXI",
                     "Operador é quem detém a instalação ou a fonte, diretamente ou por instrumento "
                     "jurídico. Em regra é a concessionária ou o parceiro privado, que responde pelas "
                     "obrigações e sanções. O contrato deve dizer isso de forma expressa."))
        ob(Obrigacao("Receitas alternativas/acessórias", "Lei 8.987/1995, arts. 11 e 18, VI",
                     "A receita de créditos deve estar prevista no edital e no contrato e favorecer "
                     "a modicidade tarifária."))
        ob(Obrigacao("Equilíbrio econômico-financeiro", "Lei 8.987/1995, arts. 9º e 10; "
                     "Lei 14.133/2021, art. 124, II, d",
                     "O novo custo regulatório do SBCE pode ensejar reequilíbrio, conforme a matriz "
                     "de riscos."))
        ob(Obrigacao("Risco de impedimento de contratar", "Lei 15.042/2024, art. 37, VI, e, e § 3º",
                     "Infração gravíssima ao SBCE pode gerar proibição de contratar com a "
                     "administração por até 3 anos. Preveja isso nas cláusulas de habilitação e "
                     "de extinção."))
    if tipo == "concessao_florestal":
        ob(Obrigacao("Titularidade dos créditos na concessão", "Lei 15.042/2024, arts. 2º, XIV, "
                     "e 43, caput e § 1º",
                     "O concessionário pode ser gerador de projeto (art. 2º, XIV). O ente público não "
                     "pode desenvolver projeto próprio em área concedida (art. 43, § 1º)."))
        ob(Obrigacao("Exploração de créditos na concessão florestal", "Lei 11.284/2006 (alterações "
                     "posteriores)", "Confira as cláusulas contratuais e a redação vigente da "
                     "Lei de Gestão de Florestas Públicas.", conferir=True))

    # --- Geração de créditos -------------------------------------------------
    if gera_creditos:
        ob(Obrigacao("Requisitos para CRVE", "Lei 15.042/2024, arts. 12, 25, 26 e 44",
                     "Metodologia credenciada pelo órgão gestor, verificação por entidade "
                     "independente e inscrição no Registro Central. O desenvolvedor e o certificador "
                     "devem ser pessoa jurídica brasileira (art. 26)."))
        ob(Obrigacao("Vedação à dupla contagem e ITMOs", "Lei 15.042/2024, arts. 2º, IX e XXXIV, e 51",
                     "O mesmo ativo não pode servir a mais de um compromisso. A transferência "
                     "internacional depende de autorização expressa e de ajuste correspondente."))
        ob(Obrigacao("Tributação", "Lei 15.042/2024, arts. 17 a 20",
                     "IR e CSLL sobre o ganho, conforme o regime (art. 17). Despesas de geração são "
                     "dedutíveis (art. 17, § 1º; art. 18). Converter crédito em CRVE não é fato "
                     "gerador (art. 17, § 4º). Não incidem PIS/Pasep e Cofins (art. 19)."))
        ob(Obrigacao("Natureza de valor mobiliário", "Lei 15.042/2024, arts. 14 a 16 e 54",
                     "Quando negociados no mercado financeiro e de capitais, os ativos são valores "
                     "mobiliários sob a CVM. A colocação privada fica fora (art. 14, parágrafo único)."))
    if terras_publicas_ou_tradicionais:
        ob(Obrigacao("Projetos públicos de crédito de carbono", "Lei 15.042/2024, art. 43, I a III e §§ 1º a 3º",
                     "Só em áreas de propriedade e usufruto cumulativos do ente (UCs e imóveis "
                     "públicos), sem sobreposição com concessão ou com terceiros. Parceria com "
                     "desenvolvedor privado exige licitação. Admite consórcio público (Lei 11.107/2005)."))
        ob(Obrigacao("Anuência do órgão gestor da área", "Lei 15.042/2024, art. 49",
                     "Projeto em área de propriedade e usufruto públicos depende de acompanhamento e "
                     "anuência prévia do órgão gestor da área."))
        ob(Obrigacao("Povos indígenas e comunidades tradicionais", "Lei 15.042/2024, arts. 43, V a VII, e 47",
                     "Titularidade originária das comunidades. Consentimento por consulta livre, "
                     "prévia e informada (Convenção 169 da OIT), custeada pelo desenvolvedor. "
                     "Cláusula garantindo ao menos 50% dos créditos de remoção e 70% dos de REDD+ "
                     "de mercado à comunidade."))
        if esfera in ("federal", "estadual", None):
            ob(Obrigacao("Programas jurisdicionais REDD+", "Lei 15.042/2024, art. 2º, XXVI, e art. 43, §§ 6º a 17",
                         "Somente União, Estados e DF (escala nacional ou estadual; titularidade do "
                         "Executivo proponente, § 13). Vedada a venda antecipada ou de resultados "
                         "futuros. Garantido o direito de exclusão incondicionada de áreas privadas e "
                         "a transparência dos contratos (§ 14)."))
        if esfera == "municipal":
            e.alertas.append("Municípios não conduzem programas jurisdicionais REDD+ (art. 2º, XXVI; "
                             "art. 43, § 13). Podem fazer projetos públicos em UCs e imóveis "
                             "municipais (art. 43, III).")
    return e


# ---------------------------------------------------------------------------
# Enquadramento por instalação (art. 30)
# ---------------------------------------------------------------------------
_RIGOR = {"nao_regulado": 0, "regulado_monitoramento": 1, "regulado_conciliacao": 2}


def enquadrar_operador(tipo: str, unidades: list[dict], **kwargs) -> dict:
    """Aplica os patamares a cada instalação ou fonte (art. 30) e consolida o operador.

    `unidades`: itens de mrv.totais_por_instalacao(). A faixa do operador é a da unidade mais
    rigorosa. A base da conciliação soma só as emissões líquidas das unidades acima do patamar
    de conciliação (arts. 30, II, e 34).
    """
    lim_m = kwargs.get("limiar_monitoramento", LIMIAR_MONITORAMENTO)
    lim_c = kwargs.get("limiar_conciliacao", LIMIAR_CONCILIACAO)
    for k in ("atividade_agropecuaria_primaria", "residuos_com_neutralizacao"):
        kwargs.pop(k, None)  # aqui essas exclusões são por unidade
    reais = [u for u in unidades if u["instalacao_id"] is not None]
    alertas: list[str] = []
    avaliadas = []
    for u in unidades:
        if u["instalacao_id"] is None and reais:
            if u["escopo1"]:
                alertas.append(f"{u['escopo1']:g} tCO2e de escopo 1 sem instalação vinculada não "
                               "foram enquadradas. Vincule as atividades às instalações ou fontes.")
            continue
        if u["instalacao_id"] is None:
            alertas.append("Nenhuma instalação cadastrada: a organização foi tratada como uma única "
                           "instalação. Cadastre as instalações e fontes para aplicar o art. 30.")
        excluida = u["agropecuaria_primaria"] or u["residuos_neutralizacao"]
        if u["agropecuaria_primaria"]:
            faixa = "nao_regulado"
            alertas.append(f"{u['nome']}: produção primária agropecuária, fora das obrigações "
                           "(art. 1º, § 2º).")
        elif u["residuos_neutralizacao"]:
            faixa = "nao_regulado"
            alertas.append(f"{u['nome']}: tratamento de resíduos/efluentes com neutralização "
                           "comprovada, fora dos patamares (art. 30, § 3º).")
        else:
            faixa = faixa_sbce(u["escopo1"], lim_m, lim_c)
        avaliadas.append({**u, "faixa_sbce": faixa, "excluida": bool(excluida)})

    pior = max(avaliadas, key=lambda a: (_RIGOR[a["faixa_sbce"]], a["escopo1"]), default=None)
    referencia = pior["escopo1"] if pior and pior["faixa_sbce"] != "nao_regulado" else \
        max((a["escopo1"] for a in avaliadas if not a["excluida"]), default=0.0)
    e = enquadrar(tipo, referencia, por_instalacao=True, **kwargs)
    e.alertas = alertas + e.alertas
    base = sum(a["escopo1_liquido"] for a in avaliadas if a["faixa_sbce"] == "regulado_conciliacao")
    total = sum(a["escopo1"] for a in avaliadas if not a["excluida"])
    if e.faixa_sbce == "nao_regulado" and total > lim_m:
        e.alertas.append(f"O total do operador ({total:g} tCO2e) passa do patamar, mas nenhuma "
                         "instalação ou fonte isolada passa: não há obrigação no SBCE (art. 30).")
    r = e.como_dict()
    r["instalacoes"] = avaliadas
    r["escopo1_total_operador"] = round(total, 6)
    r["base_conciliacao_tco2e"] = round(base, 6)
    return r


# ---------------------------------------------------------------------------
# Checagens específicas
# ---------------------------------------------------------------------------
def checar_reparticao_comunidade(tipo_projeto: str, percentual_comunidade: float) -> list[str]:
    """Art. 47, I, b: mínimo de 50% (remoção) ou 70% (REDD+ de mercado) dos créditos."""
    minimos = {"remocao": 50.0, "redd_mercado": 70.0}
    if tipo_projeto not in minimos:
        raise ValueError("tipo_projeto deve ser 'remocao' ou 'redd_mercado'.")
    if percentual_comunidade < minimos[tipo_projeto]:
        return [f"Repartição de {percentual_comunidade:g}% abaixo do mínimo legal de "
                f"{minimos[tipo_projeto]:g}% (Lei 15.042/2024, art. 47, I, b)."]
    return []


def checar_projeto_publico(*, esfera: str, proprietario_e_usufrutuario: bool,
                           sobreposicao_terceiros: bool, parceria_privada: bool,
                           licitacao: bool, anuencia_gestor_area: bool,
                           programa_jurisdicional: bool = False,
                           venda_antecipada: bool = False) -> list[str]:
    """Pendências de um projeto público de crédito de carbono. Lista vazia = sem pendências."""
    p = []
    if programa_jurisdicional:
        if esfera == "municipal":
            p.append("Município não pode conduzir programa jurisdicional REDD+ (art. 2º, XXVI; "
                     "art. 43, § 13).")
        if venda_antecipada:
            p.append("Vedada a venda antecipada ou de resultados futuros (art. 43, § 6º, I e IV).")
        return p
    if not proprietario_e_usufrutuario:
        p.append("O ente precisa ter, cumulativamente, propriedade e usufruto da área (art. 43, § 1º).")
    if sobreposicao_terceiros:
        p.append("Há sobreposição com concessão ou com terceiros: exclua a área sobreposta "
                 "(art. 43, §§ 1º e 9º).")
    if parceria_privada and not licitacao:
        p.append("Parceria com desenvolvedor privado exige licitação (art. 43, §§ 1º e 3º).")
    if not anuencia_gestor_area:
        p.append("Falta a anuência prévia do órgão gestor da área (art. 49).")
    return p
