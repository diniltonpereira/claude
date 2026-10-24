# Cerne · Guia da VPS

Do servidor zerado ao painel no ar com HTTPS, em ~15 minutos.

## 1. Contratar a VPS

- **Sistema:** Ubuntu 24.04 LTS (ou 22.04).
- **Tamanho para o piloto:** 2 vCPU, 4 GB de RAM, 40 GB de SSD. (Com 2 GB funciona; o script cria swap.)
- **Região: Brasil (São Paulo)**, para manter os dados no país (LGPD) e baixa latência para o ERP.
  Provedores com região em São Paulo: AWS Lightsail, Google Cloud, Azure, Oracle Cloud, Vultr, Magalu Cloud, Locaweb, entre outros.
- Ao criar, **cadastre sua chave SSH pública** (ou depois: `ssh-copy-id root@IP`).
- Ative **snapshots automáticos** no painel do provedor da VPS, se houver.

## 2. Apontar o domínio

No DNS do seu domínio, crie um registro **A** (e **AAAA**, se tiver IPv6):

```
cerne.seudominio.com.br   A   <IP da VPS>
```

Espere propagar (`dig +short cerne.seudominio.com.br` deve devolver o IP). O certificado HTTPS
(Let's Encrypt) é emitido automaticamente pelo Caddy no primeiro acesso.

## 3. Provisionar (uma vez)

```bash
ssh root@<IP>
curl -fsSLO https://raw.githubusercontent.com/diniltonpereira/claude/main/cerne/deploy/vps/provisionar.sh
CERNE_DOMAIN=cerne.seudominio.com.br ACME_EMAIL=voce@seudominio.com.br bash provisionar.sh
```

O script ([`vps/provisionar.sh`](vps/provisionar.sh)) é idempotente e:

1. atualiza o sistema, ajusta o fuso para America/Sao_Paulo e cria swap se houver pouca RAM;
2. cria o usuário **`cerne`** (sudo) com a sua chave SSH;
3. desliga login de root e por senha no SSH (só se a chave estiver instalada — senão avisa e mantém);
4. liga firewall **UFW** (22, 80, 443), **fail2ban** e atualizações automáticas de segurança;
5. instala **Docker** + Compose do repositório oficial, com rotação de logs;
6. clona o repositório em `/opt/cerne` (só a pasta `cerne/`), gera `deploy/.env` com **segredos aleatórios** e sobe a pilha.

Variáveis opcionais: `USUARIO`, `SSH_PORT`, `REPO_URL`, `REPO_BRANCH`, `SUBIR=nao` (só prepara a máquina).

> **Repositório privado?** O clone por HTTPS vai falhar. Siga a mensagem do script: gere uma *deploy key*
> (somente leitura) para o usuário `cerne`, cadastre-a no GitHub e rode de novo com `REPO_URL=git@github.com:...`.

Ao final, **copie `/opt/cerne/cerne/deploy/.env` para um cofre fora da VPS** (gerenciador de senhas).
Sem a `CERNE_MASTER_KEY` as credenciais de ERP cifradas no banco não podem ser lidas.

## 4. Primeiro acesso

```bash
ssh cerne@<IP>
cd /opt/cerne/cerne/deploy
bash criar-provedor.sh "Provedor Piloto Ltda" 12.345.678/0001-90   # imprime a chave de API (uma vez)
```

Abra `https://cerne.seudominio.com.br`, entre com a chave e siga o fluxo:
**Catálogo** (cadastrar e validar serviços) → **Ativações** → **Faturas** → **Visão geral** (relatório PDF).
No celular, use "Adicionar à tela inicial" para instalar o painel como aplicativo.

O `ADMIN_TOKEN` (no `.env`) também dá acesso à área *Acesso administrativo* do painel.
Para restringi-la ao seu IP, defina `ADMIN_ALLOW_IPS=203.0.113.10/32` no `.env` e rode `bash deploy.sh --sem-pull`.

## 5. Integração com o Hubsoft (quando o piloto estiver pronto)

> Só com o **Termo de Autorização de Acesso a Dados e Confidencialidade** assinado.

1. No painel, **Conta e integrações → Configurar Hubsoft** (credenciais de um usuário **somente leitura**; ficam cifradas).
2. Descubra a estrutura real das faturas (não imprime valores nem dados pessoais):
   ```bash
   docker compose run --rm api sync --inspect --provider-id 1 --period 2026-10
   ```
3. Ajuste os mapas (`config/` é montado no worker):
   ```bash
   cp config/field_map.example.json config/field_map.json      # nomes dos campos
   cp config/service_map.example.json config/service_map.json  # id do serviço no Hubsoft -> código do catálogo
   ```
   Se as rotas não baterem, ajuste `PATHS` em `api/app/integrations/hubsoft.py` (são hipóteses).
4. Teste sem gravar e depois de verdade:
   ```bash
   docker compose run --rm -v $PWD/config:/app/config/local:ro api sync --provider-id 1 --period 2026-10 \
     --field-map /app/config/local/field_map.json --service-map /app/config/local/service_map.json --dry-run
   ```
5. Para sincronizar todo dia automaticamente: `ATIVAR_SYNC=sim` no `.env` e `bash deploy.sh --sem-pull`
   (o worker processa o mês anterior de todos os provedores com credencial, a cada `SYNC_INTERVAL_HOURS`).

## 6. Operação do dia a dia

| Tarefa | Comando (em `/opt/cerne/cerne/deploy`) |
|---|---|
| Atualizar para a versão do git | `bash deploy.sh` (volta sozinho para a versão anterior se a nova não ficar saudável) |
| Ver estado | `docker compose ps` · `curl -s https://$DOMINIO/healthz` |
| Logs | `docker compose logs -f api` (sem dados pessoais nem credenciais) |
| Backup manual | `docker compose exec backup sh /usr/local/bin/backup.sh once` |
| Listar backups | `ls -lh backups/` (diários às 3h, guardados por 14 dias) |
| Restaurar backup | `docker compose exec -T db pg_restore -U cerne -d cerne --clean --if-exists < backups/cerne-AAAAMMDD-HHMMSS.dump` |
| Novo provedor | `bash criar-provedor.sh "Razão Social" CNPJ` |
| Console do banco | `docker compose exec db psql -U cerne` |

**Backups fora da VPS:** os dumps ficam em `deploy/backups/` no próprio servidor. Copie-os para outro lugar
(ex.: `rclone` para um bucket S3/Backblaze com região no Brasil, via `cron` diário) — um backup no mesmo
disco não protege contra perda da VPS.

**Deploy pelo GitHub:** o workflow *Cerne · Deploy na VPS* (manual) entra por SSH e roda `deploy.sh`.
Configure os segredos `VPS_HOST`, `VPS_USER` (`cerne`), `VPS_SSH_KEY` e `VPS_KNOWN_HOSTS`
(`ssh-keyscan <IP>`), e crie o *environment* `producao` (permite exigir aprovação antes do deploy).

## Arquitetura

```
Internet ──443──> Caddy (HTTPS automático, cabeçalhos de segurança, filtro de IP do /v1/admin)
                    ├── /v1/*, /healthz, /docs ──> api:8000 (uvicorn, 2 workers)
                    └── /*                      ──> painel estático (PWA)
api ──> db (Postgres 16, volume pgdata, sem porta exposta)
backup ──> pg_dump diário em deploy/backups/
worker (opcional) ──> Hubsoft (somente leitura) ──> separação
```
