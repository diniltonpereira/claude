# Roteiro: especificação CarbonMG × plataforma atual

Este documento registra o que foi aproveitado da especificação "Prompt mestre CarbonMG", o que foi ajustado por razão jurídica e o que falta.

## 1. Já implementado (versão 0.2)

| Item da especificação | Situação |
| --- | --- |
| Inventário por escopos, GHG Protocol e ISO 14064-1 | Feito |
| Anexo obrigatório; sem ele, "pendente" e fora do relatório | Feito |
| Fatores editáveis com versionamento e histórico | Feito (só o registrador publica) |
| IPCC AR6 | Feito, por inventário (AR5 ou AR6); conferir os valores de HFC e SF6 |
| Emissão evitada em aba própria, sem abater | Feito |
| Hierarquia órgão > secretarias/unidades > prédios | Feito |
| Painel: total, por mês, por unidade, por escopo, comparação anual, ranking, meta com progresso, alerta de variação | Feito (alerta a partir de 30%) |
| Relatório do inventário em PDF e Excel com metodologia, fatores, memória e anexos | Feito (HTML para PDF pelo navegador, e XLSX) |
| Terceirizadas: convite por CNPJ e contrato, aceite, lançamento mensal, "lançado pelo fiscal" | Feito |
| Prazo no dia 10, lembrete e marcação de inadimplência | Feito, com ajuste jurídico (ver seção 2) |
| Score 40/30/20/10 e classes A a D | Feito |
| Selo com QR Code, página pública e validade de 12 meses | Feito, com ajuste no nome |
| Notificação do fiscal que vira documento do processo | Feito |
| Escopo 3 automático a partir das terceirizadas | Feito (botão de importação; só comprovados) |
| Certificado de cancelamento com hash e verificação pública | Feito, com ajuste (ver seção 2) |
| Ledger imutável com timestamp; sem edição nem duplicidade | Feito (hash encadeado, idempotência) |
| Marcação Voluntário / Para SBCE | Feito (compensação voluntária e conciliação SBCE com regras da lei) |
| Painel público de transparência por órgão | Feito |
| Trilha: quem, quando, IP, versão anterior | Feito |
| Perfis: admin, gestor, fiscal, terceirizada, auditor, público | Feito, mais verificador e moderador para a segregação de funções |
| Cadastro com CNPJ e código IBGE | Feito (CNES pode ir no identificador da unidade) |

## 2. Ajustes jurídicos em relação à especificação

| Especificação | Como ficou | Por quê |
| --- | --- | --- |
| Decreto MG 48.938/2024 como base | Mantido como referência | O decreto trata das contratações da administração **estadual**. Prefeituras seguem a própria regulamentação da Lei 14.133 |
| "Inadimplente" se não lançar até o dia 10 | Só com `clausula_relato` no contrato; sem ela, "sem lançamento" (informativo) | Não há obrigação sem previsão contratual |
| Selo "Fornecedor Descarbonizado – Nível B" | "Selo de Gestão de Emissões — Nível B"; classe D sem selo | Evitar alegação enganosa (CDC, art. 37) |
| Aposentadoria emitida pela plataforma | Cancelamento no espelho exige o nº da aposentadoria na certificadora | Só o registro da certificadora aposenta de fato; sem isso há dupla contagem (Lei 15.042, art. 2º, IX) |
| Crédito Verra/GS "para SBCE" | Bloqueado na conciliação | Só CBE e CRVE contam (arts. 10, 34 e 44) |
| "Relatório pronto para SBCE/TCE-MG" | "Estruturado conforme a Lei 15.042/2024" | Os modelos do órgão gestor ainda não existem; não há exigência específica do TCE-MG identificada |
| Pagamento por PIX ou boleto antes de entregar | Não implementado (fase 2) | Para órgão público: contratação, empenho prévio (Lei 4.320, art. 60), cancelamento comprovado, liquidação (art. 63) e pagamento |
| Catálogo só de parceiros nomeados | Não implementado (fase 2) | Lista fechada gera risco de direcionamento; usar credenciamento aberto (Lei 14.133, art. 79) |
| Gerar ETP e TR da contratação da própria plataforma | Não implementado | Risco de impedimento e direcionamento (Lei 14.133, arts. 7º e 14). Modelos para **outras** compras do órgão podem vir na fase 3, com aviso |
| WhatsApp automático | Interface `Notificador` pronta, sem envio | Exige base legal, opt-in (LGPD) e a API oficial |

## 3. Próximas fases

### Fase 2 — Créditos e compra pública (alvo: 90 dias)
- Catálogo de lotes com **credenciamento aberto** de fornecedores e critérios objetivos.
- Filtros: projetos em MG, REDD+, energia.
- Fluxo de despesa pública:
  1. simulação de custo;
  2. minuta de TR e justificativa de preço para a compra de créditos;
  3. registro de empenho;
  4. entrega (aposentadoria comprovada);
  5. liquidação;
  6. pagamento.
- Pagamento por PIX ou boleto apenas para empresas e terceirizadas, por intermediador regulado.
- Taxa do marketplace explícita no contrato e no documento fiscal.
- Carteira do órgão: emitido, compensado, saldo a compensar e histórico (parte já existe no painel).

### Fase 3 — Projetos, documentos e campo (alvo: 180 dias)
- Projetos de geração com:
  - georreferenciamento, área, titularidade (matrícula, CAR), linha de base e estimativa;
  - funil (ideia > viabilidade > documento de concepção > validação > verificação > emissão > venda), com checklist e responsável por etapa;
  - checagens dos arts. 43, 47 e 49, que já existem em `enquadramento.py`.
- Contratos de cessão ou parceria com divisão percentual e assinatura eletrônica (gov.br ou Clicksign). Projeto público com parceiro privado só com licitação.
- Modelos de ETP, TR e relatório de sustentabilidade em DOCX editável, para compras do órgão que não sejam a contratação da própria plataforma.
- Integração com o PNCP (publicação feita pelo órgão).
- Importação de faturas CEMIG e planilhas.
- Login gov.br, PostgreSQL e PWA com lançamento offline e sincronização (Norte de MG).

### Fase 4 — SBCE e expansão (alvo: 12 meses)
- Integração com o Registro Central do SBCE quando a API oficial existir.
- Modelos oficiais de plano de monitoramento e relato do órgão gestor.
- Interoperabilidade com escrituradores e B3, se houver negociação em mercado (CVM, Lei 15.042, art. 14).
- Outros estados.

## 4. Comercial (fora do código)

Os planos e preços da especificação são decisão comercial. Em venda por pregão, o preço resulta da disputa, e os planos servem de referência na pesquisa de preços do órgão (Lei 14.133, art. 23). A plataforma não implementa cobrança.
