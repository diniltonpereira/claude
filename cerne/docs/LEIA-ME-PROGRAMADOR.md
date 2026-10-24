# Cerne · Leia-me do programador

## 1. O que é
Camada auditável para **provedores de internet (ISPs)** que:
1. registra a classificação de cada serviço como **SCM** ou **SVA**, com o fundamento e quem validou;
2. registra **ativação, consentimento e uso** do SVA por assinante;
3. **separa os valores da fatura** em SCM / SVA / PENDENTE;
4. mantém **trilha de auditoria encadeada por hash** e gera **relatório de evidências**.

Piloto previsto com um provedor médio que usa o ERP **Hubsoft**. Integração **somente leitura**.

## 2. Regras de produto que NÃO podem ser quebradas
- **A API não decide a classificação.** Aplica a que o provedor cadastrou e o contador validou. Sem regra,
  regra não validada ou SVA sem ativação → **PENDENTE** (revisão humana).
- **Minimização de dados (LGPD):** ler só identificadores e valores. **Nunca** ler/armazenar nome, CPF/CNPJ do
  assinante, endereço, telefone, e-mail, pagamento.
- **Somente leitura no ERP.** Nada de gravar, emitir nota ou cobrar no Hubsoft nesta fase.
- **Posicionamento:** "separação e comprovação fiscal-regulatória", **não** "reduzir imposto".
- Credenciais do ERP nunca em log, repositório ou mensagem.

## 3. Estado atual (v0.2)

**Feito e testado** (35 testes; SQLite e Postgres; CI em `.github/workflows/cerne-ci.yml`):

| Item do backlog original | Situação |
|---|---|
| P0.1 Rodar a API e testes de integração com isolamento entre provedores | ✅ `tests/test_api.py` (inclui isolamento em todas as rotas) |
| P0.3 Credenciais de ERP por provedor cifradas | ✅ Fernet com `CERNE_MASTER_KEY` (migrar para KMS/Vault é troca de `security.py`) |
| P0.4 Postgres + Alembic + trava por provedor na trilha | ✅ advisory lock + restrição única; teste de concorrência |
| P0.5 Chaves com rotação/revogação, rate limiting, logs sem dados pessoais | ✅ |
| P1.6 Relatório de evidências em PDF por competência | ✅ dados reais, hash do PDF registrado na trilha |
| P1.7 Endpoints de `TaxParameter` e relatório por regra | ✅ parâmetros com vigência; resumo por tipo de alerta |
| P1.8 Dockerfile, CI, backup, observabilidade, deploy | ✅ Compose + Caddy + backup diário + healthcheck + deploy com rollback |
| P1.9 Carimbo de tempo externo da cabeça da trilha | ⚠️ parcial: `GET /v1/audit/head` expõe o hash; falta enviar a uma TSA (RFC 3161) |
| P2.10 Painel web do provedor; PNG/ICO da marca | ✅ painel PWA; ícones exportados |

**Ainda pendente:**
- **P0.2 Validar o Hubsoft** com o piloto: `PATHS`, paginação, filtros de data e `field_map.json` são hipóteses.
  Precisa de credenciais somente leitura e do Termo assinado.
- Carimbo de tempo externo (P1.9), integração com outros ERPs (IXC, MK-Auth, SGP, Voalle).
- LGPD: base legal, retenção, termo de operador; revisão do critério por contador tributarista.
- Rate limit é por processo; com mais de uma VPS/instância, mover para Redis ou para o proxy.
- Backup fora da VPS (ver `deploy/README.md`, seção 6).

## 4. Critérios de aceite do MVP (piloto)
- Fatura real do provedor-piloto separada em SCM/SVA/PENDENTE, com avisos corretos, em ambiente de teste.
- `GET /v1/audit/verify` retorna íntegra após a carga; alteração manual de um registro é detectada. *(coberto por teste)*
- Nenhum dado pessoal além de identificadores no banco. *(coberto por teste no sync; auditar o banco no piloto)*
- Relatório de evidências do mês gerado e conferido por contador.

## 5. Fora do escopo desta fase
Emissão de nota fiscal, gravação no Hubsoft, carrier billing, catálogo de múltiplos SVAs de terceiros.

## 6. Documentos jurídicos (fora do pacote)
O **Termo de Autorização de Acesso a Dados e Confidencialidade** é assinado entre provedor e desenvolvedor antes
de qualquer acesso às credenciais. Não inicie testes com dados reais sem ele.

## 7. Onde está cada coisa
Ver [`../README.md`](../README.md). Relatório modelo (layout de referência, dados ilustrativos):
[`relatorio-modelo/`](relatorio-modelo/).
