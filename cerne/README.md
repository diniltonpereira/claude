# Cerne · Separação e comprovação SCM × SVA

Camada auditável para **provedores de internet (ISPs)**: registra a classificação de cada serviço como
SCM ou SVA (com fundamento e validação do contador), a ativação, o consentimento e o uso do SVA por
assinante, **separa os valores da fatura** em SCM / SVA / PENDENTE e mantém uma **trilha de auditoria
encadeada por hash**, com relatório de evidências em PDF.

> A Cerne **não decide** a classificação e não promete economia tributária. Ela aplica o que o provedor
> cadastrou e o contador validou; o que não tem base documental vai para **PENDENTE**.

## Aplicativos

| Aplicativo | Pasta | O que é |
|---|---|---|
| **API** | [`api/`](api/) | FastAPI + SQLAlchemy + Postgres (Alembic). Separação, trilha, relatórios, chaves, credenciais de ERP cifradas. |
| **Painel do provedor** | [`painel/`](painel/) | Aplicativo web instalável (PWA) servido no mesmo domínio: catálogo, ativações, faturas, auditoria, tributos, relatório PDF, chaves e integração. Inclui a área de administração (cadastro de provedores). |
| **Worker de sincronização** | [`api/app/sync.py`](api/app/sync.py) | Lê faturas do Hubsoft (somente leitura) e separa automaticamente. Roda como serviço opcional (`--profile sync`). |
| **Infraestrutura da VPS** | [`deploy/`](deploy/) | Docker Compose (Postgres, API, Caddy com HTTPS automático, backup diário), provisionamento do servidor, deploy com rollback. |

Também: [`marca/`](marca/) (identidade visual, PNG/ICO exportados) e [`docs/`](docs/) (guia do programador e relatório modelo).

## Estrutura

```
cerne/
├── api/                    # backend (Python 3.12)
│   ├── app/
│   │   ├── core.py         # separação e hash — sem dependências, regra de ouro do produto
│   │   ├── mapping.py      # fatura do ERP -> formato da API (minimização LGPD)
│   │   ├── models.py       # tabelas
│   │   ├── security.py     # chaves de API, limite de requisições, cifragem Fernet
│   │   ├── routers/        # admin, conta, catálogo, ativações, faturas, auditoria, tributos, relatórios
│   │   ├── services/       # trilha (com trava por provedor), separação, relatórios/PDF
│   │   ├── integrations/   # Hubsoft (somente leitura)
│   │   └── sync.py         # job de sincronização
│   ├── migrations/         # Alembic
│   ├── tests/              # 35 testes (unitários + integração + concorrência no Postgres)
│   └── Dockerfile
├── painel/                 # HTML/CSS/JS puro, sem build
├── deploy/                 # docker-compose.yml, Caddyfile, scripts de VPS, backup
├── marca/
└── docs/
```

## Desenvolvimento local

```bash
cd cerne/api
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest                       # SQLite
export ADMIN_TOKEN=dev CERNE_MASTER_KEY=$(openssl rand 32 | base64 | tr '+/' '-_')
uvicorn app.main:app --reload          # http://127.0.0.1:8000/docs
```

O painel chama a API no mesmo endereço; para usá-lo localmente suba a pilha completa
(`deploy/` com `CERNE_DOMAIN=localhost`) ou sirva `painel/` atrás de um proxy que encaminhe `/v1`.

## Produção

Guia passo a passo da VPS: **[`deploy/README.md`](deploy/README.md)**.
Resumo: `provisionar.sh` numa VPS Ubuntu nova → DNS apontando → `https://seu-dominio` no ar com HTTPS,
backup diário e atualizações por `deploy.sh` (ou pelo workflow *Cerne · Deploy na VPS*).
