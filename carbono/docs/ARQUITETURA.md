# Arquitetura do Lastro Carbono

Guia para quem vai manter e evoluir o sistema. Leia junto com `docs/MARCO_LEGAL.md`: várias regras do código existem por exigência legal e não devem ser "simplificadas".

## 1. Visão geral

```
Navegador (painel sem build: HTML + CSS + JS puro)
        │  fetch JSON, cabeçalho X-Usuario-Id (piloto)
        ▼
FastAPI  plataforma/api.py  ── validação (Pydantic), autorização por papel, IP para auditoria
        │
        ▼
Regras de negócio (funções puras sobre sqlite3.Connection)
  mrv · fatores · calculo · anexos · terceirizadas · painel · enquadramento · compensacao · registro · relatorio · auditoria
        │
        ▼
SQLite (WAL)  plataforma/banco.py  ── invariantes no esquema: CHECK, UNIQUE, gatilhos de imutabilidade
Disco          CARBONO_ANEXOS/<sha256>  ── comprovantes
```

A escolha é deliberadamente simples. As regras não dependem do framework web: a API só traduz HTTP em chamadas de função. Para trocar SQLite por PostgreSQL, ver a seção 7.

## 2. Módulos

| Arquivo | Responsabilidade |
| --- | --- |
| `banco.py` | Esquema (versão 13), conexão, transação com `BEGIN IMMEDIATE`, exceções de domínio |
| `fatores.py` | Fatores versionados e conjuntos de GWP (AR5 e AR6); carga inicial |
| `calculo.py` | Cálculo puro por fator ou por gás, com memória de cálculo |
| `anexos.py` | Upload com checagem de tipo e conteúdo, SHA-256, controle de acesso, detecção de adulteração, revarredura e bloqueio |
| `assinatura.py` | Assinatura avançada gov.br: identidade e nível da conta no Login Único, autorização no ITI, assinatura do SHA-256 do PDF, conferência do PKCS#7 (hash, assinatura e CPF do certificado) e conclusão da parceria |
| `creditos.py` | Conferência documental de créditos voluntários (link do registro, bloco de séries, quantidade, safra, bloqueio de série repetida ou sobreposta), conferência do fiscal antes da liquidação e credenciamento de verificadores com validade |
| `verificacao.py` | Pacote de evidências para o organismo verificador (ISO 14064-3): inventário, relatórios, comprovantes, trilha e SHA256SUMS |
| `geo.py` | Polígono da área: leitura de shapefile do CAR, KML/KMZ e GeoJSON; validação (território, UTM, contorno que se cruza); área na esfera autálica; interseção (Shapely/GEOS) |
| `envio.py` | E-mail por caixa de saída: fila gravada na transação do ato, despacho SMTP com novas tentativas, preferência do titular e anonimização |
| `antivirus.py` | Cliente do ClamAV (protocolo INSTREAM do `clamd`, falha fechada) e recusa de conteúdo ativo: JavaScript e anexos em PDF, macros e bombas de compressão em XLSX, imagens corrompidas ou gigantes |
| `auditoria.py` | Trilha somente-inclusão; IP via `ContextVar` preenchida por middleware |
| `mrv.py` | Unidades e hierarquia, atividades (inclusão, edição, exclusão), totais, fluxo de verificação e aprovação |
| `painel.py` | Painel do gestor, metas, transparência por órgão |
| `enquadramento.py` | SBCE: faixas por instalação, fases do art. 50, obrigações, checagens dos arts. 43, 47 e 49 |
| `terceirizadas.py` | Contratos, lançamentos, situação mensal, score, selo, notificações, rotina diária, importação para o escopo 3 |
| `registro.py` | Lotes de créditos, transferência, cancelamento, livro-razão encadeado, certificado por hash |
| `compensacao.py` | Plano e execução da compensação voluntária e da conciliação SBCE |
| `pncp.py` | PNCP pelas APIs públicas: vínculo do pedido à contratação divulgada (confere órgão e situação) e pesquisa de preços com itens de contratações similares (valor homologado ou estimado, por tCO2e, válida por 12 meses) |
| `modelos.py` | Modelos editáveis em Word (.docx): ETP da compensação (art. 18, § 1º), Termo de Referência (art. 6º, XXIII) e relatório de emissões, com campos a preencher destacados |
| `relatorio.py` | Relatório HTML para impressão em PDF e planilha XLSX |
| `projetos.py` | Projetos de geração: cadastro, funil com checklist e evidência, parceria, travas dos arts. 43, 47 e 49 |
| `acesso.py` | Sessões (cookie e hash), CSRF, senha scrypt, TOTP, bloqueio, convites, gov.br (OIDC com PKCE, id_token conferido pela chave publicada, nível da conta, logout no Login Único), termos e direitos LGPD |
| `operacao.py` | Criação do administrador, backup e restauração verificados |
| `faturas.py` | Leitura de faturas de energia (PDF e planilha), análise (unidade, duplicidade, ano) e lançamento no escopo 2; interface `FonteFaturas` para futura API da distribuidora |
| `marketplace.py` | Credenciamento, ofertas, catálogo, pedido no rito da despesa pública, carteira, minuta de TR |
| `api.py` | Rotas REST e páginas estáticas em `/painel` |
| `demo.py` | Cenário fictício completo |
| `web/` | Painel (`index.html`, `app.css`, `app.js`); `sw.js` (service worker: app e consultas offline), `offline.js` (fila de envio em IndexedDB), `manifest.webmanifest` e ícones (PWA); `mapa.js` (mapa da área) e `vendor/leaflet` (Leaflet 1.9.4, servido pela própria plataforma, conferido pelo hash publicado) |

## 3. Papéis e permissões

| Ação | Gestão do órgão | Fiscal | Terceirizada | Verificador | Moderador | Administração | Auditor | Público |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Unidades, inventário e atividades da própria organização | ✓ | | | | | unidades | | |
| Ver inventários | própria org. | própria org. | | fila e todos | fila e todos | todos | todos | aprovados (resumo) |
| Submeter / verificar / aprovar | submeter | | | verificar ou devolver | aprovar ou devolver | | | |
| Convidar terceirizada | ✓ | ✓ | | | | | | |
| Lançar emissões do contrato | | ✓ (marcado) | ✓ (após aceite) | | | | | |
| Emitir selo | ✓ | ✓ | | | | | | |
| Notificação formal | | ✓ | | | | | | |
| Compensar (cancelar créditos) | ✓ | | conta própria | | | | | |
| Escriturar lotes, cadastrar, publicar fatores | | | | | | ✓ | | |
| Pedir credenciamento, ofertar, entregar pedido | | | ✓ (vendedor) | | | | | |
| Analisar credenciamento | | | | | | ✓ | | |
| Abrir pedido, instruir, empenhar, pagar, cancelar | ✓ | | | | | | | |
| Atestar liquidação | | ✓ | | | | | | |
| Cadastrar e conduzir projeto de geração | ✓ (titular) | | ✓ (titular) | | | | | |
| Trilha de auditoria | | | | | | ✓ | ✓ | |
| Verificar selo ou certificado, painel público | | | | | | | | ✓ |

Regras de segregação (Lei 14.133/2021, art. 7º, § 1º, e Lei 15.042/2024, arts. 32 e 44):
- quem elabora não verifica nem aprova;
- o verificador precisa estar marcado como acreditado e não pode pertencer à organização inventariada;
- o moderador não pode ter verificado o mesmo inventário.

O auditor não grava nada. As funções de escrita checam o papel, e o upload recusa o auditor.

## 4. Regras que o banco garante (não as remova)

- **Fatores:** `fator_emissao` não aceita alterar o valor nem apagar (gatilhos), e só há uma versão vigente por código (índice único parcial). Cada `atividade.fator_id` aponta para a versão exata usada.
- **Comprovação:** atividade sem `anexo_id` (e sem `contrato_id`) é pendente e fica fora de todos os totais, relatórios e do painel (`mrv.COMPROVADA`).
- **Imutabilidade:** `anexo`, `selo`, `movimento` e `auditoria` são somente inclusão (gatilhos).
- **Livro-razão:** cada movimento guarda o hash do anterior, e `verificar_cadeia` recalcula tudo.
- **Crédito voluntário:** sem `certificadora` e `certificado_anexo_id` não pode ser escriturado. Cancelado, exige `registro_externo_ref` (CHECK no esquema).
- **Saldo:** nunca fica negativo, e um lote cancelado não volta a circular. Transferências e cancelamentos usam `BEGIN IMMEDIATE` e chave de idempotência.
- **Pedido de compra:** o banco recusa estados sem os dados da etapa (CHECKs): não há empenho sem número, entrega sem lote, liquidação sem nota fiscal e fiscal, pagamento sem referência, nem empenho menor que o total. O empenho reserva a quantidade da oferta; o cancelamento devolve.
- **Versão do esquema (hoje 13):** bancos a partir da versão 2 são migrados automaticamente ao conectar (`banco.migrar`, numa transação). Toda mudança em tabela existente precisa de uma função em `MIGRACOES`. Versões mais antigas são recusadas com `EsquemaIncompativel`.

## 5. Modelo de dados (resumo)

```
organizacao ─┬─ usuario (papel)            fator_emissao (codigo, versao, vigente)   gwp (conjunto, gas)
             ├─ instalacao (pai_id: hierarquia; natureza: unidade | instalacao | fonte_movel)
             ├─ inventario (ano, gwp_conjunto, status) ─┬─ atividade (mes, escopo, natureza, fator_id, anexo_id, contrato_id)
             │                                          └─ evento_mrv (trilha de aprovação)
             ├─ meta (ano_base, ano_alvo, %)
             ├─ conta ─ lote (tipo_ativo, certificadora, status, registro_externo_ref) ─ movimento (hash encadeado)
             ├─ credenciamento (status, documentos, criterios, valido_ate)
             ├─ oferta (lote_id, categoria, uf, preco_unitario, quantidade_disponivel)
             ├─ pedido (oferta_id, quantidade, valores, taxa, status, processo, empenho, nota_fiscal, pagamento)
             ├─ projeto (tipo, natureza_area, lat/long, etapa, anuencia_ref, consulta, certificadora) ─┬─ projeto_item (etapa, item, anexo)
             │                                                                                     └─ parceria (desenvolvedor, %, licitação, averbação, assinatura)
             └─ contrato (orgao_id, fornecedor_id, vigência, clausula_relato, dia_limite)
                   ├─ lancamento_fornecedor (ano, mes, fator_id, anexo_id, lancado_pelo_fiscal)
                   ├─ acao_fornecedor (anexo obrigatório)
                   ├─ score_fornecedor (ano, mes, score, classe, componentes)
                   ├─ selo (codigo, classe, validade, hash)
                   └─ notificacao (lembrete | alerta | notificacao_formal)
anexo (sha256, tipo, enviado_por)          auditoria (quem, quando, ip, acao, entidade, antes, depois)
```

Correspondência com o modelo do prompt Lastro Carbono:

| Prompt | Aqui |
| --- | --- |
| `Organization` | `organizacao` + `instalacao` |
| `Contract` | `contrato` |
| `EmissionEntry` | `atividade` e `lancamento_fornecedor` |
| `EmissionFactor` | `fator_emissao`, versionado |
| `CreditLot` | `lote` |
| `Purchase` | `pedido` (rito da despesa pública) + `movimento` (entrega no registro) |
| `Score` | `score_fornecedor` |
| `Project` | `projeto` + `projeto_item` + `parceria` |

## 6. Score da terceirizada (`terceirizadas.calcular_score`)

| Componente | Peso | Regra |
| --- | --- | --- |
| Regularidade | 40 | Meses vencidos dos últimos 12 com lançamento comprovado (com anexo) |
| Redução | 30 | Últimos 12 meses contra os 12 primeiros do contrato. Cheio com 10% de redução, zero com aumento de 10% ou mais, linear entre eles. Com menos de 24 meses de histórico, metade (neutro) |
| Compensação | 20 | Créditos cancelados pela própria terceirizada nos últimos 12 meses, proporcionais às emissões (limite de 100%) |
| Ações | 10 | 2,5 por ação com evidência nos últimos 12 meses, até 4 |

Classes: A (80 a 100), B (60 a 79), C (40 a 59), D (até 39, sem selo). Uma queda para C ou D gera alerta ao fiscal. Os critérios de redução e de ações não vieram do prompt: são escolhas documentadas, ajustáveis em `PESOS` e na função.

## 7. Produção

O guia operacional está em [`IMPLANTACAO.md`](IMPLANTACAO.md): configuração, Docker, proxy TLS, gov.br, rotinas, backup e restauração, controles de segurança e checklist LGPD. O modo produção é o padrão da API (`CARBONO_MODO=producao`); o modo demonstração (`--demo`) aceita o cabeçalho `X-Usuario-Id` e existe só para testes.
