# Implantação do CarbonMG em produção

Roteiro para colocar o piloto no ar com uma prefeitura. Tudo aqui foi testado com a imagem Docker do projeto. A integração real com o gov.br depende de credenciais que só o órgão obtém; o fluxo foi testado com um provedor simulado.

## 1. Arquitetura do piloto

```
Internet ──TLS──► proxy reverso (Caddy/Nginx/balanceador) ──HTTP──► contêiner carbonmg :8000
                                                                     ├─ volume /dados  (carbono.db + anexos/)
                                                                     └─ pasta /backups (cópias diárias)
```

**Banco: SQLite em modo WAL.** Para um órgão no piloto, com dezenas de usuários, é suficiente, simples de operar e tem backup consistente sem parar o sistema.

**Quando migrar para PostgreSQL:**
- mais de um órgão grande na mesma instância;
- escrita concorrente intensa (erros `database is locked` nos logs);
- exigência de alta disponibilidade com réplica.

A migração exige trocar `?` por `%s`, `sqlite3.Row` por um cursor de dicionário, `BEGIN IMMEDIATE` por `SELECT … FOR UPDATE` e os gatilhos para PL/pgSQL. As regras de negócio não mudam.

## 2. Passo a passo

### 2.1 Configuração
```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # cole em CARBONO_SEGREDO
```
Preencha:
- `CARBONO_URL_PUBLICA`: o endereço público, usado nos QR Codes e nos convites;
- `CARBONO_CONTROLADOR` e `CARBONO_ENCARREGADO`: dados exigidos pela LGPD;
- as variáveis `GOVBR_*`, depois da seção 3.

**O `CARBONO_SEGREDO` não pode ser trocado depois:** ele gera os códigos HMAC dos CPFs, e trocá-lo invalida o login gov.br de todos. Guarde-o num cofre de senhas. Sem ele, o sistema não sobe em modo produção.

### 2.2 Subir
```bash
docker compose build
docker compose run --rm carbonmg python -m plataforma criar-admin --email adm@orgao.mg.gov.br --nome "Administração"
docker compose up -d
```
O `criar-admin` pede a senha (mínimo de 12 caracteres) e mostra o segredo da verificação em duas etapas, que deve ser cadastrado num aplicativo autenticador. A administração não entra sem esse código.

**Rede com proxy corporativo que inspeciona TLS:** passe a CA do proxy apenas durante a construção da imagem:

```bash
docker build --secret id=ca,src=ca-do-proxy.crt --build-arg HTTPS_PROXY=http://proxy:3128 .
```

### 2.3 Proxy reverso com TLS (exemplo com Caddy)
```
carbonmg.orgao.mg.gov.br {
    reverse_proxy 127.0.0.1:8000
}
```
O cookie de sessão é `Secure`: em produção, o acesso **tem** de ser por HTTPS. A variável `CARBONO_COOKIE_INSEGURO=1` serve só para teste local e nunca deve ser usada em produção.

### 2.4 Primeiros cadastros (pelo painel, como administração)
1. Organizações: prefeitura, organismo de inspeção acreditado, terceirizadas.
2. Usuários:
   - servidores: informe o CPF, que é guardado só como código, para entrarem com gov.br;
   - empresas: informe o e-mail e clique em **Gerar convite**. Envie o link à pessoa; ele vale 72 horas e só pode ser usado uma vez.
3. Fatores de emissão: publique os valores oficiais vigentes (MCTI, Programa Brasileiro GHG Protocol).

## 3. Login gov.br

1. O **órgão** solicita a integração com o Login Único no portal gov.br e cadastra:
   - URL de retorno: `https://SEU_ENDERECO/auth/govbr/retorno`;
   - escopos: `openid email profile govbr_confiabilidades_idtoken` (o último traz o nível da conta, usado na assinatura).
2. Primeiro em homologação (`GOVBR_URL=https://sso.staging.acesso.gov.br`), depois em produção (`https://sso.acesso.gov.br`).
3. Cadastre também a **URL de logout**: `https://SEU_ENDERECO/painel/` (ou o valor de `GOVBR_LOGOUT_REDIRECT_URI`). Quem entrou pelo gov.br, ao sair, passa pelo logout do Login Único, como a integração exige.
4. Preencha `GOVBR_CLIENT_ID`, `GOVBR_CLIENT_SECRET` e `GOVBR_REDIRECT_URI` no `.env` e reinicie. O botão "Entrar com o gov.br" aparece sozinho.

**Como funciona:**
- fluxo authorization code com PKCE (S256), `state` e `nonce` de uso único, válidos por 10 minutos;
- o `id_token` é conferido com a chave pública publicada em `/jwk` (assinatura, emissor, destinatário, validade e `nonce`);
- o CPF vem do `id_token` e é comparado por HMAC. Não há autocadastro: o servidor precisa ter sido cadastrado antes.

**Confira no roteiro técnico vigente** (acesso.gov.br/roteiro-tecnico) os caminhos `/authorize`, `/token`, `/jwk` e `/logout` e o emissor (`iss`). Se mudarem, ajuste `acesso.config_govbr` ou defina `GOVBR_ISSUER`.

## 4. Rotinas

| Rotina | Comando | Frequência |
| --- | --- | --- |
| Lembretes, alertas e score das terceirizadas | `docker compose exec carbonmg python -m plataforma rotina-diaria` | diária (cron do servidor) |
| Entrega de e-mails | automática pelo `servir`, a cada minuto (ou `enviar-emails` no cron, com `CARBONO_ENVIO_AUTOMATICO=0`) | contínua |
| Revarredura dos anexos com as assinaturas do dia | `docker compose exec carbonmg python -m plataforma varrer-anexos` | diária, depois da atualização do ClamAV |
| Backup | `docker compose exec carbonmg python -m plataforma backup --destino /backups` | diária; copie a pasta `backups/` para outro local em região BR |
| Teste de restauração | ver 4.1 | mensal |
| Atualização de versão | `git pull && docker compose build && docker compose up -d` | as migrações do banco rodam sozinhas ao iniciar |

### 4.1 Restaurar
```bash
docker compose stop carbonmg
docker compose run --rm carbonmg python -m plataforma restaurar /backups/carbonmg-AAAAMMDDTHHMMSSZ.db
docker compose start carbonmg
```
**O que a restauração confere antes de substituir o banco:**
- o SHA-256 do manifesto;
- o `integrity_check` do SQLite;
- a cadeia de hashes do livro-razão.

O banco anterior fica salvo com o sufixo `.antes-da-restauracao`.

## 5. Segurança aplicada

| Controle | Como |
| --- | --- |
| Autenticação | gov.br (servidores) ou e-mail com senha scrypt e 12+ caracteres; TOTP obrigatório para a administração e opcional para os demais (em "Minha conta") |
| Força bruta | 5 falhas bloqueiam por 15 minutos; o código TOTP não pode ser reutilizado |
| Sessão | cookie `HttpOnly`, `Secure` e `SameSite=Lax`; no banco só o hash do token; 8 h de validade, 1 h de inatividade; sessões encerradas ao trocar a senha ou anonimizar |
| CSRF | toda escrita exige `X-CSRF-Token` igual ao da sessão |
| Cabeçalhos | CSP restrita (sem script embutido), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS |
| Contêiner | usuário sem privilégios (uid 10001), healthcheck, porta publicada só em 127.0.0.1 |
| Logs | JSON por requisição (método, caminho, status, tempo), sem parâmetros nem dados pessoais |
| Anexos | tipo e assinatura do arquivo conferidos, até 10 MB, antivírus e conteúdo ativo (seção 5.1), guardados pelo SHA-256 e verificados ao baixar |

**Pendentes recomendados:**
- WAF no proxy;
- monitoramento externo de `/saude`;
- armazenamento de anexos em S3 compatível com criptografia, se o volume crescer.

### 5.1 Antivírus e conteúdo ativo nos anexos

- **ClamAV:** o `compose.yaml` sobe o serviço `clamav` (imagem oficial `clamav/clamav:1.4`), que atualiza as assinaturas sozinho a cada 2 horas. O servidor precisa de saída para `database.clamav.net`. A primeira carga das assinaturas leva alguns minutos e usa cerca de 1,5 GB de RAM; o `carbonmg` só sobe quando o antivírus está pronto.
- **Exame antes de gravar:** cada arquivo vai ao `clamd` pela rede interna do Docker, antes de ser salvo. Arquivo infectado é recusado (HTTP 409), não é gravado e a tentativa fica na trilha de auditoria (`anexo_recusado`, com o nome da ameaça e o SHA-256).
- **Falha fechada:** em produção, sem `CARBONO_CLAMAV` a aplicação não sobe; se o antivírus cair, os envios são recusados com HTTP 503 ("tente de novo em alguns minutos") e o `/saude` responde `degradado` (503), o que aciona o monitoramento. Só `CARBONO_ANTIVIRUS=desativado` dispensa o exame; registre essa decisão.
- **Conteúdo ativo é recusado mesmo sem vírus conhecido** (faturas, notas e certidões não precisam dele):
  - PDF com JavaScript, ação que abre programas, arquivo embutido, mídia, formulário XFA ou envio de formulário, inclusive com nomes escapados (`/J#61vaScript`);
  - planilha com macro ou ActiveX, ou que se expande demais ao abrir (bomba de compressão);
  - imagem corrompida ou com dimensões absurdas; CSV com conteúdo binário.
  
  Quem recebe a recusa deve gerar o documento de novo com "imprimir como PDF" ou salvar a planilha como `.xlsx` sem macros.
- **Revarredura diária:** `varrer-anexos` reexamina todos os arquivos guardados com as assinaturas do dia. O que for detectado fica **bloqueado para download** (`anexo_bloqueado` na trilha), sem apagar o registro: o comprovante continua citado no inventário, e a administração decide o que fazer. Para retirar um bloqueio indevido (falso positivo), apague a linha em `anexo_alerta` e registre o motivo.
- **Teste:** o arquivo de teste EICAR é detectado como `Eicar-Test-Signature` puro ou dentro de um ZIP/XLSX. Colado no meio de um PDF falso, o ClamAV não o acusa (o EICAR só vale no início do arquivo); isso é o comportamento normal do padrão, não falha da integração. Para conferir na instalação:
  ```bash
  CARBONO_CLAMAV_REAL=127.0.0.1:3310 python -m pytest tests/test_antivirus.py -k real
  ```

## 6. LGPD: checklist do órgão
- [ ] Definir controlador e encarregado (`CARBONO_CONTROLADOR`, `CARBONO_ENCARREGADO`): aparecem na política e no aceite.
- [ ] Revisar o texto da política (`acesso.politica_privacidade`) com a assessoria jurídica e, se mudar, trocar `CARBONO_TERMOS_VERSAO`. Todos voltam a aceitar.
- [ ] Atender pedidos dos titulares:
  - exportação: o próprio usuário faz em "Minha conta";
  - anonimização: a administração faz em Cadastros. Os atos ficam na trilha (LGPD, art. 16, I).
- [ ] Registrar as operações de tratamento (art. 37) e avaliar se cabe um relatório de impacto (art. 38).
- [ ] E-mails (seção 10): confirmar com a assessoria jurídica a base legal (execução do contrato e obrigação legal, LGPD, art. 7º, II e V, e art. 23). Lembretes e alertas podem ser desligados pelo titular; convites, notificações formais e avisos de segurança não.
- [ ] WhatsApp: ainda sem envio; exigiria opt-in e a API oficial.

## 7. Uso em campo, sem conexão (PWA)

- **Instalar no celular:** abra o endereço no Chrome (Android) ou no Safari (iPhone) e use "Adicionar à tela inicial" ou "Instalar app". O modo offline exige HTTPS, que o proxy da seção 2.3 já oferece.
- **O que funciona sem sinal:**
  - abrir o app;
  - consultar inventários, contratos, unidades, painel, projetos e fatores que já foram abertos com conexão naquele aparelho;
  - lançar atividades do inventário e lançamentos de terceirizadas, com foto ou PDF do comprovante.
- **O que não funciona sem sinal:** aprovar, verificar, comprar, cancelar créditos, gerar relatórios e qualquer outra ação. Essas ações exigem o servidor e mostram "Sem conexão".
- **Envio:**
  - os lançamentos offline ficam na fila do aparelho (IndexedDB) e são enviados sozinhos quando a conexão volta, ou pelo botão "Enviar agora" em "Para enviar";
  - cada um leva uma chave única: se o envio for repetido (por exemplo, a resposta se perdeu), o servidor devolve o lançamento já gravado, sem duplicar;
  - um lançamento recusado pelo servidor (exemplo: inventário já submetido) fica marcado com o motivo e pode ser descartado.
- **Sessão:** se ela expirar enquanto o aparelho está sem sinal, a fila é mantida. Depois de entrar de novo, o envio continua.
- **Privacidade:**
  - a fila é separada por usuário;
  - ao sair, as consultas guardadas são apagadas do aparelho;
  - anexos baixados, exportação de dados pessoais e trilha de auditoria nunca são guardados offline;
  - em aparelho compartilhado, oriente o usuário a sair ao terminar.
- **Atualização do app:** o service worker usa o app guardado e busca a nova versão em segundo plano, então a atualização aparece na abertura seguinte. Ao mudar arquivos do painel, troque `VERSAO` em `plataforma/web/sw.js` para descartar o cache antigo de imediato.

## 8. Faturas de energia (CEMIG)

- **Não há API pública da CEMIG para faturas.** A plataforma lê o PDF da fatura e planilhas com a relação de contas. Se o órgão obtiver acesso por API ou arquivo em lote pelo canal de grandes clientes, implemente `faturas.FonteFaturas` e reutilize `faturas.lancar`.
- **Preparação:** em "Unidades e instalações", informe em cada prédio o nº da instalação na CEMIG (UC). Assim as faturas caem automaticamente na unidade certa.
- **PDF:** o sistema lê a instalação, o mês de referência e o consumo, mostra o trecho de origem e o grau de confiança, e **a pessoa confere antes de lançar**. Regras de leitura:
  - o "consumo médio diário" é ignorado;
  - no grupo A, os consumos na ponta e fora de ponta são somados;
  - nas linhas de TE e TUSD, que repetem a quantidade, usa-se a maior, sem somar;
  - em unidade com geração distribuída, há um aviso para lançar o consumo **registrado** (energia retirada da rede).
- **PDF escaneado** (imagem, sem texto) não é lido: digite os valores no formulário de lançamento.
- **Layout novo:** se a CEMIG mudar o layout e a leitura falhar, ajuste as expressões em `faturas.PADROES` e acrescente um caso em `tests/test_faturas.py` com o texto da fatura nova.
- **Planilha (CSV ou XLSX):**
  - colunas obrigatórias: Instalação (ou UC), Referência (mês/ano) e Consumo (kWh);
  - a prévia mostra cada linha como pronta, sem unidade vinculada, já lançada ou com erro;
  - o lançamento é tudo ou nada.
- **Sem duplicidade:** a mesma UC no mesmo mês não entra duas vezes no inventário. O PDF ou a planilha fica como comprovante (SHA-256).

## 9. Modo demonstração
`python -m plataforma demo --banco demo.db` e `python -m plataforma servir --banco demo.db --demo`.

Nesse modo, a entrada é pelo número do usuário (cabeçalho `X-Usuario-Id`), sem senha. **Nunca use em produção.** O modo produção é o padrão e ignora esse cabeçalho.

## 10. E-mails

- **Configuração:** preencha `CARBONO_SMTP_*` no `.env` com o SMTP do órgão ou de um serviço contratado (com contrato que trate de dados pessoais, LGPD art. 39). Use um remetente do domínio do órgão (`nao-responda@...`) e configure SPF, DKIM e DMARC nesse domínio, ou os e-mails cairão no spam. A senha do SMTP vai só no `.env`, nunca no repositório.
- **O que é enviado:**

| Mensagem | Para quem | Pode ser desligada? |
| --- | --- | --- |
| Convite com o link para criar a senha | o usuário convidado | não |
| Lembrete do prazo de lançamento (até o dia limite do contrato) | usuários da terceirizada | sim, em "Minha conta" |
| Alerta de mês sem lançamento e de queda do score | fiscais do órgão contratante | sim, em "Minha conta" |
| Notificação formal do fiscal (texto e prazo) | usuários da terceirizada | não: é ato do processo |
| Aviso de segurança (senha definida, verificação em duas etapas ativada) | o próprio usuário | não |

- **Como funciona (caixa de saída):** a mensagem é gravada junto com o ato que a gera. Se o ato for desfeito, não há e-mail; se o SMTP estiver fora do ar, a mensagem espera. O envio é tentado de novo após 5, 10, 20, 40 e 80 minutos; recusa definitiva do servidor (endereço inexistente) não é repetida.
- **Acompanhamento:** em **Cadastros**, a seção "E-mails enviados pela plataforma" mostra fila, enviados e falhas, com o motivo. Corrija o e-mail do usuário e use "Tentar de novo". O conteúdo não aparece nessa tela, e o link do convite é apagado do banco assim que o e-mail é entregue.
- **Sem SMTP configurado:** nada se perde. O convite continua mostrando o link para envio manual, e a fila sai quando o SMTP for configurado. Os lembretes e alertas também continuam visíveis na página do contrato.
- **Teste local:** `docker run -d -p 1025:1025 -p 8025:8025 axllent/mailpit` e `CARBONO_SMTP_HOST=127.0.0.1`, `CARBONO_SMTP_PORTA=1025`, `CARBONO_SMTP_SEGURANCA=nenhuma`. As mensagens aparecem em http://127.0.0.1:8025.

## 11. Mapa e polígono da área dos projetos

- **Como delimitar:** na página do projeto, em "Área do projeto", desenhe no mapa (cada clique marca um vértice) ou importe o arquivo. Antes de salvar, a plataforma mostra a área calculada, a diferença para a área declarada, se o ponto de localização ficou fora e as sobreposições com outros projetos.
- **Arquivos aceitos:**
  - shapefile do CAR (o .zip baixado do SICAR, com `.shp`, `.shx`, `.dbf` e `.prj`);
  - KML e KMZ (Google Earth);
  - GeoJSON.
  
  As coordenadas devem ser geográficas (SIRGAS 2000 ou WGS 84). Arquivo em UTM é recusado com a orientação para exportar de novo.
- **Versões:** cada alteração grava uma versão nova (com SHA-256) e as anteriores ficam no histórico e na trilha de auditoria. Depois da validação, o polígono não muda mais na plataforma.
- **Regras:**
  - restauração florestal e PSA precisam do polígono para entrar no documento de concepção;
  - sobreposição com outro projeto **do mesmo tipo**, a partir de 1 ha ou de 1% da menor área, impede a validação (dupla contagem);
  - sobreposições menores (ajuste de divisa) e com projetos de outro tipo só aparecem como informação;
  - para quem não é do órgão, o projeto vizinho aparece só pelo número, sem o nome.
- **Área calculada:** é uma conferência, com erro típico abaixo de 0,5%. A área oficial continua sendo a do CAR, da matrícula ou do levantamento georreferenciado.
- **Fundo do mapa:** o padrão é o OpenStreetMap, adequado ao piloto pela política de uso justo do OSM. Em produção, configure `CARBONO_MAPA_TILES` com um provedor contratado ou com o servidor de mapas do Estado (modelo XYZ, `{z}/{x}/{y}`). Imagem de satélite ajuda a desenhar a área, mas confira a licença do provedor. Sem acesso ao fundo, o polígono continua aparecendo sobre fundo liso.
- **Acessibilidade:** desenhar exige mouse ou toque; a importação do arquivo é o caminho equivalente para quem usa teclado ou leitor de tela.

## 12. Assinatura eletrônica gov.br dos contratos de parceria

Assinatura eletrônica **avançada** (Lei 14.063/2020, art. 4º, II), gratuita, pela API de assinatura do ITI, conforme o "Roteiro de Integração API Assinatura avançada gov.br".

- **Credenciais:**
  - só um **gestor público** pode pedir, pelo "Serviço de Integração aos Produtos do Ecossistema da Conta Digital GOV.BR" (portal gov.br). A homologação sai em até 3 dias úteis e a produção em até 5;
  - **a produção exige** que o sistema já esteja integrado ao Login Único (seção 3) e hospedado em **domínio oficial de governo** (por exemplo, `carbonmg.valeverde.mg.gov.br`), conforme os arts. 3º e 5º da Portaria SGD/MGI nº 7.076/2024;
  - cadastre a URL de retorno `https://SEU_ENDERECO/assinaturas/iti/retorno` e preencha `ITI_CLIENT_ID`, `ITI_CLIENT_SECRET` e `ITI_REDIRECT_URI`. Os endereços padrão são os de homologação; em produção, defina `ITI_OAUTH_URL` e `ITI_API_URL` com os informados junto com a credencial.
- **Como funciona para quem assina:**
  1. o titular envia o contrato final em PDF na página do projeto ("Enviar para assinatura");
  2. cada parte (titular e desenvolvedor) clica em "Assinar com o gov.br". Quem entrou com e-mail e senha também passa pelo Login Único nesse momento;
  3. conta **bronze** é barrada antes da autorização, com a mensagem e o link oficiais para elevar o nível;
  4. a pessoa autoriza o uso do certificado gov.br (código no aplicativo gov.br ou SMS) e volta à plataforma;
  5. quando todas as partes assinam, a parceria fica marcada como assinada e o funil do projeto segue.
- **O que a plataforma confere antes de guardar cada assinatura:** que o pacote PKCS#7 assina exatamente o SHA-256 do PDF, que a assinatura confere com a chave do certificado e que o CPF do certificado é o da conta gov.br usada. Na primeira assinatura, o CPF (só como HMAC) fica vinculado ao usuário; nas seguintes, precisa ser o mesmo.
- **Formato:** assinatura **destacada**, um arquivo `.p7s` por parte, junto do PDF. Para validar juridicamente, inclusive a cadeia do certificado, envie o PDF e os `.p7s` ao validador do ITI: `validar.iti.gov.br` (produção) ou `validar.staging.iti.br` (homologação). Os links ficam na página do projeto.
- **Homologação no gov.br:** o processo pede um vídeo com a barra de endereços visível, mostrando:
  - entrada pelo gov.br e entrada por e-mail e senha;
  - conta bronze barrada;
  - conta prata ou ouro assinando;
  - download do PDF e dos `.p7s`;
  - logout.
  
  O roteiro está todo coberto pelo painel.
- **Sem credenciais:** o botão de assinatura não aparece. Fica disponível "Assinado fora da plataforma? Registre a referência", para contratos assinados em outro sistema (SEI, assinador ICP-Brasil).
- **Ainda não implementado:**
  - assinatura embutida no próprio PDF (PAdES);
  - assinatura qualificada ICP-Brasil (escopo `icp_brasil` da mesma API);
  - um provedor privado (como a Clicksign) para partes que não tenham conta gov.br prata ou ouro.

## 13. Modelos editáveis em Word

| Documento | Onde | Base |
| --- | --- | --- |
| Estudo Técnico Preliminar da compra de créditos | página do inventário aprovado, "ETP da compensação (Word)" | Lei 14.133/2021, art. 18, § 1º; traz os incisos I, IV, VI, VIII e XIII, que o § 2º torna obrigatórios |
| Termo de Referência | página do pedido, "Termo de Referência (Word)" | Lei 14.133/2021, art. 6º, XXIII, alíneas "a" a "j" |
| Relatório de emissões | página do inventário, "Relatório editável (Word)" | GHG Protocol e ABNT NBR ISO 14064-1, com o enquadramento no SBCE |

- **Números da plataforma:** emissões por escopo, quantidade a compensar (escopos 1 e 2, menos o que já foi compensado), pesquisa de preços do catálogo por categoria, fatores com versão e fonte, trilha de verificação e aprovação.
- **Campos em amarelo `[PREENCHER: …]`:** o que só a área demandante sabe (processo, plano de contratações anual, dotação, fiscal, outras fontes de preço, conclusão).
- **São minutas:** o ETP e o TR trazem o aviso de controle prévio de legalidade (art. 53). O TR é neutro: não exige compra por esta plataforma nem cita fornecedores. Os textos foram escritos a partir da estrutura dos artigos; **confira a redação vigente da lei** antes de usar.
