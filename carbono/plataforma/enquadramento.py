"""Enquadramento legal: SBCE (Lei 15.042/2024) e regime do contratante público.

Este módulo traduz regras jurídicas em checagens automáticas. Ele NÃO substitui
parecer jurídico: os limiares e obrigações dependem de regulamentação do órgão
gestor do SBCE ainda em curso. Os pontos marcados com `conferir=True` não foram
confirmados no texto oficial nesta versão (veja docs/MARCO_LEGAL.md).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Limiares de emissão por operador (tCO2e/ano), Lei 15.042/2024.
LIMIAR_MONITORAMENTO = 10_000   # plano de monitoramento + relato anual
LIMIAR_CONCILIACAO = 25_000     # além disso, conciliação periódica de obrigações

TIPOS_ORGANIZACAO = ("orgao_publico", "ppp", "concessao", "concessao_florestal", "empresa")


@dataclass
class Obrigacao:
    titulo: str
    fundamento: str
    detalhe: str
    conferir: bool = False


@dataclass
class Enquadramento:
    emissoes_escopo1_tco2e: float
    faixa_sbce: str
    obrigacoes: list[Obrigacao] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        return asdict(self)


def faixa_sbce(emissoes_escopo1: float) -> str:
    if emissoes_escopo1 > LIMIAR_CONCILIACAO:
        return "regulado_conciliacao"
    if emissoes_escopo1 > LIMIAR_MONITORAMENTO:
        return "regulado_monitoramento"
    return "nao_regulado"


def enquadrar(tipo: str, emissoes_escopo1: float, *, atividade_agropecuaria_primaria: bool = False,
              gera_creditos: bool = False, terras_publicas_ou_tradicionais: bool = False
              ) -> Enquadramento:
    if tipo not in TIPOS_ORGANIZACAO:
        raise ValueError(f"Tipo de organização inválido: {tipo!r}")
    if emissoes_escopo1 < 0:
        raise ValueError("Emissões não podem ser negativas.")

    faixa = "nao_regulado" if atividade_agropecuaria_primaria else faixa_sbce(emissoes_escopo1)
    e = Enquadramento(round(emissoes_escopo1, 3), faixa)
    ob = e.obrigacoes.append

    # --- SBCE -------------------------------------------------------------
    if atividade_agropecuaria_primaria:
        e.alertas.append("Produção primária agropecuária fica fora das obrigações do SBCE; "
                         "pode, porém, gerar créditos/CRVE.")
    if faixa in ("regulado_monitoramento", "regulado_conciliacao"):
        ob(Obrigacao("Plano de monitoramento", "Lei 15.042/2024 (operador > 10.000 tCO2e/ano)",
                     "Submeter ao órgão gestor plano de monitoramento das emissões das instalações."))
        ob(Obrigacao("Relato anual de emissões e remoções", "Lei 15.042/2024",
                     "Enviar relato anual verificado conforme regulamento do órgão gestor."))
    if faixa == "regulado_conciliacao":
        ob(Obrigacao("Conciliação periódica de obrigações", "Lei 15.042/2024 (operador > 25.000 tCO2e/ano)",
                     "Entregar CBE (e CRVE no limite admitido pelo regulamento) em quantidade "
                     "igual às emissões do período; relato de conciliação."))
    if faixa != "nao_regulado":
        ob(Obrigacao("Regime sancionador", "Lei 15.042/2024",
                     "Advertência, multa (até 3% do faturamento bruto; R$ 50 mil a R$ 20 milhões "
                     "quando não houver faturamento), embargo, entre outras.", conferir=True))
    elif emissoes_escopo1 > LIMIAR_MONITORAMENTO * 0.8 and not atividade_agropecuaria_primaria:
        e.alertas.append("Emissões acima de 80% do limiar de 10.000 tCO2e: monitorar tendência.")

    # --- Contratante público ---------------------------------------------
    if tipo == "orgao_publico":
        ob(Obrigacao("Política Nacional sobre Mudança do Clima", "Lei 12.187/2009",
                     "Compatibilizar ações do órgão com as metas nacionais (NDC) e planos setoriais."))
        ob(Obrigacao("Contratações sustentáveis", "Lei 14.133/2021, arts. 5º e 11, IV",
                     "Desenvolvimento nacional sustentável como princípio e objetivo da licitação; "
                     "critérios ambientais e ciclo de vida no ETP e no termo de referência."))
        ob(Obrigacao("Compensação voluntária exige base legal e motivação",
                     "CF, art. 37; Lei 14.133/2021; Lei 4.320/1964",
                     "Compra de créditos precisa de dotação orçamentária, justificativa de "
                     "interesse público e processo de contratação (ou hipótese de dispensa)."))
    if tipo == "ppp":
        ob(Obrigacao("Repartição de riscos e de ganhos", "Lei 11.079/2004, art. 5º, III",
                     "Contrato deve dispor sobre risco regulatório-climático (custo de CBE) e sobre "
                     "a titularidade e a repartição de eventuais receitas de créditos."))
    if tipo in ("concessao", "ppp", "concessao_florestal"):
        ob(Obrigacao("Receitas alternativas/acessórias", "Lei 8.987/1995, arts. 11 e 18, VI",
                     "Receita com créditos de carbono deve estar prevista no edital/contrato e "
                     "favorecer a modicidade tarifária."))
        ob(Obrigacao("Equilíbrio econômico-financeiro", "Lei 8.987/1995, arts. 9º e 10; "
                     "Lei 14.133/2021, art. 124, II, d",
                     "Novo custo regulatório do SBCE pode ensejar pedido de reequilíbrio, conforme "
                     "a matriz de riscos."))
        ob(Obrigacao("Identificar o operador regulado", "Lei 15.042/2024",
                     "Definir em contrato quem é o operador da instalação perante o SBCE "
                     "(normalmente a concessionária) e quem responde pela conciliação."))
    if tipo == "concessao_florestal":
        ob(Obrigacao("Créditos em concessão florestal", "Lei 11.284/2006, alterada pela Lei 14.590/2023",
                     "Concessionário pode explorar créditos de carbono e serviços ambientais "
                     "conforme o contrato; titularidade e repartição devem estar expressas.",
                     conferir=True))

    # --- Geração de créditos ---------------------------------------------
    if gera_creditos:
        ob(Obrigacao("Metodologia credenciada e registro central", "Lei 15.042/2024",
                     "Para virar CRVE, o crédito precisa de metodologia credenciada, verificação "
                     "independente e registro no Sistema de Registro Central do SBCE."))
        ob(Obrigacao("Vedação à dupla contagem", "Lei 15.042/2024; Acordo de Paris, art. 6",
                     "Mesmo resultado não pode ser usado duas vezes; transferência internacional "
                     "exige autorização e ajuste correspondente."))
    if terras_publicas_ou_tradicionais:
        ob(Obrigacao("Direitos de povos indígenas e comunidades tradicionais",
                     "Lei 15.042/2024; Convenção 169 da OIT",
                     "Consulta livre, prévia e informada e repartição mínima de benefícios "
                     "prevista na lei.", conferir=True))
        ob(Obrigacao("Créditos em bens públicos", "Lei 15.042/2024; Lei 14.119/2021 (PSA)",
                     "Titularidade do ente público, programa jurisdicional e procedimento "
                     "competitivo para outorga a terceiros.", conferir=True))
    return e
