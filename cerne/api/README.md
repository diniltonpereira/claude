# Cerne API (v0.2)

FastAPI + SQLAlchemy 2 + Postgres 16 (Alembic). SQLite só para desenvolvimento e testes.
Documentação interativa em `/docs`.

## Rodar e testar

```bash
pip install -r requirements-dev.txt
ruff check .
python -m pytest                                          # SQLite
TEST_DATABASE_URL=postgresql+psycopg://u:s@host/db python -m pytest   # Postgres (inclui teste de concorrência)
export ADMIN_TOKEN=dev CERNE_MASTER_KEY=$(openssl rand 32 | base64 | tr '+/' '-_')
uvicorn app.main:app --reload
```

Produção: `DATABASE_URL=postgresql+psycopg://...`, `alembic upgrade head` (o contêiner faz isso ao subir).
Nova migração: altere `models.py` e rode `alembic revision --autogenerate -m "descrição"`; o CI roda `alembic check`.

## Variáveis de ambiente

| Variável | Uso |
|---|---|
| `DATABASE_URL` | padrão `sqlite:///./cerne.db` |
| `ADMIN_TOKEN` | token do operador da plataforma (`X-Admin-Token`); vazio = admin desligado |
| `CERNE_MASTER_KEY` | chave Fernet que cifra as credenciais de ERP |
| `RATE_LIMIT_PER_MIN` | requisições por minuto por chave, por processo (padrão 240; 0 desliga) |
| `AUTO_CREATE_SCHEMA` | cria tabelas sem Alembic (padrão: só em SQLite) |
| `DOCS_ENABLED` | expõe `/docs` e `/openapi.json` (padrão `true`) |

## Autenticação

- **Provedor:** cabeçalho `X-API-Key: cerne_<prefixo>_<segredo>`. Só o SHA-256 é guardado. O provedor pode gerar
  várias chaves e revogar as antigas (rotação sem parada); não é possível revogar a última ativa.
- **Administração:** `X-Admin-Token`. Cria provedores (devolve a primeira chave), emite e revoga chaves, desativa provedores.

## Endpoints

| Método e rota | Descrição |
|---|---|
| `GET /healthz` | saúde (inclui banco) |
| `POST /v1/admin/providers` · `GET /v1/admin/providers` | cadastrar / listar provedores |
| `POST /v1/admin/providers/{id}/keys` · `DELETE /v1/admin/keys/{id}` | emitir / revogar chave |
| `POST /v1/admin/providers/{id}/deactivate` | desativar provedor |
| `GET /v1/me` | provedor da chave |
| `GET/POST /v1/keys` · `DELETE /v1/keys/{id}` | chaves do próprio provedor |
| `GET/PUT/DELETE /v1/erp/credentials` | credenciais do Hubsoft (cifradas; nunca devolvidas) |
| `POST /v1/services` · `GET /v1/services[/{code}]` | catálogo |
| `PUT /v1/services/{code}` | alterar; mudar `kind`/`criteria` gera nova versão da regra e **zera a validação** |
| `POST /v1/services/{code}/validate` · `/deactivate` | validar (CRC/OAB) · desativar |
| `POST /v1/activations` · `GET /v1/activations?customer_ref=` | ativação de SVA |
| `POST /v1/activations/{id}/usage` · `/cancel` | uso · cancelamento |
| `GET /v1/evidence/{customer_ref}` | evidências do assinante |
| `POST /v1/invoices/split` | separa SCM / SVA / PENDENTE (+ avisos e código de alerta por linha) |
| `GET /v1/invoices?period=&only_pending=` · `GET /v1/invoices/{ref}?period=` | separações vigentes / detalhe com histórico |
| `GET /v1/audit[?before_id=&action=]` · `/verify` · `/head` | trilha, verificação, cabeça (para carimbo de tempo externo) |
| `POST/GET /v1/tax-parameters` · `POST /v1/tax-parameters/{id}/close` | alíquotas por vigência (sem sobreposição) |
| `GET /v1/reports/{period}/summary` | resumo da competência |
| `GET /v1/reports/{period}/evidence.pdf` | Relatório de Evidências (registra `report.generated` com o SHA-256 do PDF) |

## Regras da separação

Sem regra, regra não validada, serviço inativo ou SVA sem ativação → **PENDENTE**.
SVA ativo sem uso no período → conta como SVA, com alerta. Reprocessar uma fatura grava nova separação;
vale a mais recente (o histórico fica visível).

## Trilha de auditoria

Cada provedor tem sua cadeia SHA-256. No Postgres, `pg_advisory_xact_lock` serializa gravações do mesmo
provedor e a restrição única `(provider_id, prev_hash)` impede bifurcação mesmo se a trava falhar
(coberto por `tests/test_concorrencia.py`, 80 gravações paralelas).

## Sincronização com o Hubsoft

Ver `python -m app.sync --help` e a seção 5 de [`../deploy/README.md`](../deploy/README.md).
Rotas e paginação em `app/integrations/hubsoft.py` **ainda são hipóteses** a confirmar com o piloto.
