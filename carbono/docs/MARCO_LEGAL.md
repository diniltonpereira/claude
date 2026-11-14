# Marco legal do carbono aplicado ao setor público, PPPs e concessões

> **Situação (14/11/2026).** Os artigos da Lei 15.042/2024 citados aqui foram conferidos no texto integral (versão compilada do Planalto, que já registra a ADI 7795 sobre o art. 56). Normas de outras leis aparecem como referência e estão indicadas quando não foram conferidas. Os pontos que dependem de regulamento do órgão gestor ficam parametrizados no código. Este documento não substitui parecer jurídico.

## 1. Lei 15.042/2024: o que a plataforma aplica

### 1.1 Âmbito e conceitos

| Tema | Dispositivo | Na plataforma |
| --- | --- | --- |
| Aplica-se a atividades, fontes e instalações em território nacional, sob responsabilidade de **operadores** | art. 1º, § 1º; art. 2º, XXI | Concessionária ou parceiro privado que detém a instalação é o operador regulado |
| Produção primária agropecuária fora das obrigações | art. 1º, §§ 2º e 3º | `atividade_agropecuaria_primaria` |
| Remoções em áreas rurais integradas podem entrar na conciliação, a critério do operador | art. 1º, § 4º | Atividade com `remocao=True` |
| Remoção excedente não vira CRVE automaticamente | art. 1º, § 5º | Remoção só reduz a base líquida e não gera ativo |
| **CBE**: direito de emitir 1 tCO2e, outorgado gratuita ou onerosamente | art. 2º, VI; art. 11 | `tipo_ativo = CBE` |
| **CRVE**: redução ou remoção verificada de 1 tCO2e, com metodologia credenciada e registro no SBCE | art. 2º, III; arts. 12 e 44 | `tipo_ativo = CRVE` |
| **Crédito de carbono** (fora do SBCE). É fruto civil quando florestal de preservação ou reflorestamento | art. 2º, VII | `tipo_ativo = VOLUNTARIO` |
| **Cancelamento**: anulação do ativo para comprovar compromisso | art. 2º, II | Operação `cancelar` (substitui o antigo "aposentar") |
| **Dupla contagem** vedada | art. 2º, IX; art. 25, III | Ativo cancelado nunca volta a circular |
| **Conciliação**: titularidade de ativos igual às **emissões líquidas** | art. 2º, V e XI; art. 34 | Base = `escopo1_liquido` |
| Rastreabilidade eletrônica de emissão, detenção, transferência e cancelamento | art. 5º, VIII; art. 23 | Livro-razão com encadeamento SHA-256 |
| Dados abertos no Registro Central | art. 24, VI | `GET /registro/cancelamentos` e `GET /registro/auditoria` públicos |

### 1.2 Quem é regulado e o que deve fazer

| Patamar (por instalação ou fonte) | Obrigações | Dispositivo |
| --- | --- | --- |
| > 10.000 tCO2e/ano | Plano de monitoramento, relato anual e demais obrigações | arts. 29, I, II e IV, e 30, I |
| > 25.000 tCO2e/ano | Tudo acima, mais o **relato de conciliação periódica** | arts. 29, III, 30, II, e 34 |

- **Os patamares podem ser majorados** por ato do órgão gestor (art. 30, § 1º), nunca reduzidos. O código recusa valor menor que o legal.
- Só valem para atividades com **metodologia de MRV consolidada** (art. 30, § 2º).
- **Exceção relevante para o setor público:** unidades de tratamento e destinação de resíduos sólidos e efluentes que comprovem sistemas de neutralização ficam fora dos patamares (art. 30, § 3º). Isso interessa a aterros e ETEs em concessão de saneamento. Ver `residuos_com_neutralizacao`.
- O relato passa por **organismo de inspeção acreditado** (art. 32, parágrafo único). Os dados vão para a conta do operador no Registro Central (art. 33).

### 1.3 Uso de ativos

- **Conciliação:** só ativos do SBCE, CBE e CRVE (arts. 10 e 34). A CRVE é limitada ao **percentual máximo do PNA** (arts. 12, I, e 21, IV), regra que o órgão gestor fixa (art. 8º, XXIV). No código, `limite_crve`.
- **Validade da CBE:** vale no período de compromisso em que foi gerada. Para outros períodos, só com autorização do PNA (art. 11, § 2º). No código, `periodo_compromisso` e `cbe_outros_periodos`.
- **Compensação voluntária:** créditos voluntários e também ativos do SBCE. O uso de CBE ou CRVE para esse fim **exige cancelamento no Registro Central** (art. 45). *Correção em relação à versão anterior da plataforma, que barrava CBE: a lei não proíbe.* O ente pode adotar política mais restritiva (`tipos_aceitos`).
- **ITMOs:** dependem de autorização formal e de ajuste correspondente (arts. 2º, XXXIV, e 51). Emitir CRVE não gera direito a essa autorização (art. 51, § 2º).

### 1.4 Implantação (art. 50)

Vigência em 12/12/2024 (art. 58). O código calcula a fase (`GET /sbce/calendario`):

| Fase | Duração legal | Com prorrogação da fase I |
| --- | --- | --- |
| I: regulamentação | 12 meses + 12 prorrogáveis | até 12/12/2026 |
| II: operacionalização dos instrumentos de relato | 1 ano | até 12/12/2027 |
| III: somente plano de monitoramento e relato | 2 anos | até 12/12/2029 |
| IV: 1º PNA, CBE gratuita, mercado de ativos | vigência do PNA | a partir de 12/12/2029 |
| V: implementação plena | após o 1º PNA | — |

Na plataforma, plano e relato ficam `exigivel=True` a partir da fase III, e a conciliação a partir da fase IV. **A contagem sequencial das fases II a IV é interpretação.** O art. 50 não diz expressamente o termo inicial de cada uma, e a prorrogação da fase I depende de ato formal. Por isso os dois pontos são parâmetros.

### 1.5 Sanções (arts. 35 a 41)

- **Penalidades (art. 37):**
  - advertência;
  - multa;
  - publicação da decisão, só na reincidência de infrações graves;
  - embargo;
  - suspensão;
  - restritivas de direitos, inclusive **proibição de contratar com a administração pública por até 3 anos** (VI, *e*), que só se aplica a infrações gravíssimas após esgotados os recursos (§ 3º).
- **Multa para pessoa jurídica:** não inferior ao custo das obrigações descumpridas e até **3% do faturamento bruto do grupo** no ano anterior, corrigido pela Selic. Na reincidência, pode subir progressivamente **até 4%** (§ 1º, I).
- **Multa sem faturamento:** de **R$ 50 mil a R$ 20 milhões** (§ 1º, II).
- **Reincidência:** nova infração em até 5 anos do trânsito em julgado administrativo (art. 38, § 2º).
- **Processo:** defesa em 30 dias (art. 38) e recurso em 20 dias (art. 38, § 5º), com duplo grau (art. 35).
- **Infrações leves:** notificação prévia para saneamento (arts. 40 e 41).
- **Mercado de capitais:** para evitar punição dupla, a competência é exclusiva da CVM (art. 38, § 4º).

**Para PPPs e concessões:** a sanção do art. 37, VI, *e*, atinge a capacidade da concessionária de contratar com o poder público. O tema deve constar das cláusulas de habilitação, de obrigações e de extinção.

### 1.6 Tributação e mercado de capitais

- **IR e CSLL sobre o ganho, conforme o caso (arts. 17 e 20):**
  - regime do contribuinte, para o desenvolvedor que emitiu;
  - ganhos líquidos, em bolsa;
  - ganho de capital, nos demais casos.
- **Despesas de geração:** dedutíveis (art. 17, § 1º). O cancelamento para compensação ou conciliação permite a dedução no lucro real (art. 18).
- **Conversão de crédito em CRVE:** não é fato gerador (art. 17, § 4º).
- **PIS/Pasep e Cofins:** não incidem sobre as receitas de alienação (art. 19).
- **Valores mobiliários:** são valores mobiliários quando negociados no mercado financeiro e de capitais (arts. 14 e 54). A colocação privada fica fora da CVM (art. 14, parágrafo único).
- **Tributo sobre emissões:** é vedada qualquer tributação sobre emissões de atividades reguladas pelo SBCE, e os limites de emissão são competência exclusiva da União (art. 22). **Estados e municípios não podem criar "taxa de carbono" sobre fontes reguladas.**
- **Art. 56** (aplicação obrigatória por seguradoras): **declarado inconstitucional (ADI 7795)**. Não o use como fundamento.

## 2. Ente público como gerador de créditos (art. 43 e seguintes)

### 2.1 Titularidade originária (art. 43, caput)

| Área | Titular |
| --- | --- |
| Terras devolutas, UCs federais e imóveis com propriedade **e** usufruto da União | União (I) |
| UCs e imóveis estaduais ou distritais nas mesmas condições | Estados e DF (II) |
| UCs e imóveis municipais nas mesmas condições | Municípios (III) |
| Terras indígenas | Comunidades indígenas (V) |
| UCs de uso sustentável com populações tradicionais | Comunidades extrativistas e tradicionais (VI) |
| Territórios quilombolas | Comunidades quilombolas (VII) |
| Lotes de assentamento | Assentados (VIII) |
| Demais imóveis públicos com usufruto de terceiro | O usufrutuário (IX) |

O **concessionário** pode ser gerador (art. 2º, XIV). O ente **não** pode desenvolver projeto próprio em área concedida (art. 43, § 1º).

### 2.2 Projetos públicos (art. 43, §§ 1º a 3º, 5º, 8º, 9º e 15; art. 49)

1. **Onde:** só nas áreas dos incisos I a III do art. 43, com propriedade e usufruto cumulativos do ente e sem sobreposição com concessão ou com terceiros. Havendo sobreposição parcial, o projeto é feito no restante da área (§ 9º).
2. **Como:** execução direta ou **parceria com desenvolvedor privado mediante licitação** (§ 1º). Admite consórcio público entre esferas (§§ 2º e 3º; Lei 11.107/2005).
3. **Anuência:** depende de acompanhamento e **anuência prévia do órgão gestor da área** (art. 49).
4. **Registro:** dispensa a averbação no registro de imóveis (§ 5º).
5. **Uso dos recursos:** podem pagar indenizações de desapropriação ou de regularização fundiária em UCs (§§ 8º e 15).

Implementado em `checar_projeto_publico` e `POST /checagens/projeto-publico`.

### 2.3 Programas jurisdicionais REDD+ (art. 2º, XXV e XXVI; art. 43, §§ 6º a 17)

- **Quem:** só em escala **nacional ou estadual**. A titularidade é do Executivo federal, estadual ou distrital proponente (§ 13). **Municípios não conduzem programas jurisdicionais.**
- **Vendas:** vedada a venda antecipada ou de resultados futuros (§ 6º, I e IV). São admitidos contratos sobre créditos de resultados já verificados (§ 6º, II).
- **Exclusão:** é direito do proprietário, usufrutuário ou concessionário excluir sua área a qualquer tempo, de forma imediata e incondicionada. Venda posterior à comunicação é nula (§ 6º, V; § 7º).
- **Transparência:** garantida desde a estruturação (§ 14), e os titulares de áreas recebem receitas proporcionais à vegetação remanescente (§ 17).
- **Conversão em CRVE:** só pelas metodologias credenciadas, com oitiva e registro na CONAREDD+ (art. 12, parágrafo único).

### 2.4 Povos indígenas e comunidades tradicionais (arts. 4º, VIII e IX; 25, § 2º; 47)

- **Consentimento:** por consulta livre, prévia e informada (Convenção 169 da OIT). O custo é do desenvolvedor, com participação do MPI, da Funai e da 6ª CCR do MPF (art. 47, I, *a*, e parágrafo único).
- **Repartição mínima em cláusula contratual:** **50% dos créditos de remoção** e **70% dos de REDD+ de mercado** para a comunidade (art. 47, I, *b*). Implementado em `checar_reparticao_comunidade`.
- **Indenização:** cláusula obrigatória por danos coletivos (art. 47, II, *b*).

## 3. Contratante público, PPPs e concessões (outras leis)

| Norma | Aplicação | Conferida? |
| --- | --- | --- |
| Lei 12.187/2009 (PNMC), art. 4º, VIII | Estímulo ao SBCE como objetivo da PNMC (redação do art. 52 da Lei 15.042) | Sim, pelo texto do art. 52 |
| Lei 14.133/2021, arts. 5º, 7º, § 1º, 11, IV, e 124, II, *d* | Desenvolvimento sustentável; segregação de funções (base do fluxo de moderação); reequilíbrio | Referência |
| Lei 11.079/2004, art. 5º, III | Repartição de riscos, incluindo o custo de CBE | Referência |
| Lei 8.987/1995, arts. 9º, 10, 11 e 18, VI | Equilíbrio econômico-financeiro; receitas alternativas que revertem em modicidade tarifária | Referência |
| Lei 11.284/2006 | Exploração de créditos em concessão florestal: confira a redação vigente e o contrato | **Não conferida** |
| Lei 4.320/1964 e CF, art. 37 | A compra de créditos pelo ente exige dotação, motivação e contratação | Referência |

**Compra de créditos pelo ente:** é despesa pública. A liquidação deve ser comprovada pelo **cancelamento em nome do ente** (art. 45). A plataforma só compensa inventário aprovado e publica o lastro.

## 4. Pendências

1. **Regulamentação do órgão gestor:**
   - patamares majorados (art. 30, § 1º);
   - percentual de CRVE do PNA (art. 21, IV);
   - modelos de plano e de relato (arts. 31 e 32);
   - acreditação de organismos de inspeção (art. 32, parágrafo único);
   - regras de transferência e cancelamento (art. 13).
2. **Ato de prorrogação da fase I** e o termo inicial das fases seguintes (art. 50).
3. **Integração** com o Registro Central (art. 23) e interoperabilidade com escrituradores (art. 15, § 2º).
4. **Conferir** a Lei 11.284/2006 e as cláusulas das concessões florestais.
5. **Enquadramento por instalação** (art. 30), em vez do total da organização.
