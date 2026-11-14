# Marco legal do carbono aplicado ao setor público, PPPs e concessões

> **Situação desta análise (14/11/2026).** O texto oficial da Lei 15.042/2024 no Planalto não pôde ser lido nesta sessão porque o acesso ao site estava bloqueado. Os pontos marcados com ⚠️ vieram de fontes secundárias e precisam ser conferidos no texto oficial e nos atos do órgão gestor antes de qualquer uso em parecer. O código marca os mesmos pontos com `conferir=True` (`plataforma/enquadramento.py`).

## 1. Lei 15.042/2024: o Sistema Brasileiro de Comércio de Emissões (SBCE)

| Tema | Regra | Como a plataforma trata |
| --- | --- | --- |
| Governança | CIM (diretrizes e Plano Nacional de Alocação), órgão gestor (regula, registra, fiscaliza e sanciona) e Comitê Técnico Consultivo Permanente | Papel `registrador` espelha o registro oficial |
| Ativos | **CBE** (Cota Brasileira de Emissões) e **CRVE** (Certificado de Redução ou Remoção Verificada de Emissões). Créditos voluntários só viram CRVE com metodologia credenciada e registro central | `tipo_ativo` = `CBE`, `CRVE` ou `VOLUNTARIO` |
| Operador > 10.000 tCO2e/ano | Plano de monitoramento e relato anual de emissões e remoções | `faixa_sbce = regulado_monitoramento` |
| Operador > 25.000 tCO2e/ano | Além do relato, conciliação periódica de obrigações (entrega de ativos iguais às emissões) | `faixa_sbce = regulado_conciliacao` e compensação com `finalidade=conciliacao_sbce` |
| Exclusão | Produção primária agropecuária fica fora das obrigações (pode gerar créditos) | `atividade_agropecuaria_primaria=True` |
| Uso de CRVE na conciliação | Admitido no limite que o órgão gestor fixar | Parâmetro `limite_crve` (padrão 0) |
| Dupla contagem | Vedada; transferência internacional (art. 6 do Acordo de Paris) exige autorização e ajuste correspondente | Unidade aposentada ou cancelada nunca volta a circular; livro-razão com hash |
| Sanções ⚠️ | Advertência, multa, publicação da decisão, embargo, entre outras. Multa de até 3% do faturamento bruto; sem faturamento, de R$ 50 mil a R$ 20 milhões. Fontes divergem sobre o agravamento por reincidência | Obrigação "Regime sancionador" |
| Implantação ⚠️ | Cinco fases. A regulamentação (fase I) tem 12 meses prorrogáveis por mais 12, ou seja, até dez./2026. Relato a partir de 2027 e teto com Plano Nacional de Alocação por volta de 2029–2030, conforme fontes secundárias | Limiares parametrizados em constantes |
| Povos indígenas e comunidades tradicionais ⚠️ | Consulta livre, prévia e informada (Convenção 169 da OIT) e repartição mínima de benefícios em projetos nas suas terras | Alerta ao marcar `terras_publicas_ou_tradicionais` |
| Programas jurisdicionais ⚠️ | Estados e União podem desenvolver programas jurisdicionais de REDD+ e há regras para créditos em bens públicos | Idem |
| Tributação e CVM ⚠️ | Tratamento de IR e PIS/Cofins sobre a negociação; caracterização como valor mobiliário quando negociados no mercado financeiro | Fora do escopo do MVP. Exige parecer |

**Atenção ao cronograma:** a regulamentação está em curso em 2026, com consultas públicas sobre cobertura setorial. Os limiares de 10 mil e 25 mil tCO2e e o limite de CRVE devem ser revistos quando saírem os atos definitivos. Os valores estão em `LIMIAR_MONITORAMENTO`, `LIMIAR_CONCILIACAO` e no parâmetro `limite_crve`.

## 2. Órgãos públicos

1. **Política Nacional sobre Mudança do Clima (Lei 12.187/2009):** orienta a ação do Estado e dá fundamento aos inventários de emissões dos entes públicos.
2. **Lei 14.133/2021:** o desenvolvimento nacional sustentável é princípio (art. 5º) e objetivo da licitação (art. 11, IV). Isso justifica critérios de emissões no ETP, no termo de referência e na avaliação do ciclo de vida. A segregação de funções (art. 7º, §1º) inspira o fluxo de moderação: quem elabora não verifica nem aprova.
3. **Comprar créditos para compensar** é despesa pública. Exige dotação, motivação de interesse público, processo de contratação ou dispensa fundamentada e liquidação comprovada pela **aposentadoria no registro, em nome do ente**. A plataforma só compensa inventário **aprovado** e publica o lastro em `GET /registro/aposentadorias`, o que serve à transparência ativa da Lei 12.527/2011.
4. **Ente público como regulado:** um órgão com instalação própria acima de 10 mil tCO2e/ano, como um aterro ou uma frota muito grande, também é operador no SBCE. O enquadramento é feito por organização e deve ser refinado por instalação.

## 3. PPPs (Lei 11.079/2004)

- **Matriz de riscos (art. 5º, III):** o custo de CBE e o risco regulatório-climático precisam ser alocados expressamente. Sem isso, o tema volta como pleito de reequilíbrio.
- **Titularidade dos créditos:** definir quem é titular dos créditos gerados pelo objeto da PPP (por exemplo, captação de metano em aterro ou eficiência energética) e como a receita é repartida com o poder concedente.
- **Operador regulado:** o contrato deve dizer quem responde perante o SBCE pela instalação, normalmente o parceiro privado.

## 4. Concessões comuns (Lei 8.987/1995)

- **Receitas alternativas e acessórias (arts. 11 e 18, VI):** a receita de créditos deve estar prevista no edital e no contrato e reverter em modicidade tarifária.
- **Equilíbrio econômico-financeiro (arts. 9º e 10; Lei 14.133/2021, art. 124, II, *d*):** custo superveniente de conciliação no SBCE pode fundamentar revisão, conforme a matriz de riscos.
- **Concessões florestais (Lei 11.284/2006, alterada pela Lei 14.590/2023) ⚠️:** permitem ao concessionário explorar créditos de carbono e serviços ambientais nos termos do contrato. Este é o principal vetor de **geração** de créditos pelo poder público.

## 5. Normas técnicas de aferição

- GHG Protocol e Programa Brasileiro GHG Protocol (FGV): escopos 1, 2 e 3, CO2 biogênico relatado à parte e GWP do AR5.
- ABNT NBR ISO 14064-1, -2 e -3: inventário, projetos e verificação. O verificador precisa ser independente e credenciado (OVV).
- Fator do SIN publicado pelo MCTI (escopo 2): atualizar todo ano em `plataforma/fatores.py`.

## 6. Pendências para validar

1. Ler o texto integral da Lei 15.042/2024 e conferir os números de artigo de cada item ⚠️.
2. Acompanhar o decreto regulamentador e as resoluções do órgão gestor e do CTCP de 2026 (cobertura setorial, limiares, limite de CRVE, credenciamento de metodologias e de OVV).
3. Interoperabilidade com o Sistema de Registro Central do SBCE quando a API oficial for publicada.
4. Parecer tributário sobre a aquisição e a alienação de ativos por entes públicos e concessionárias.
