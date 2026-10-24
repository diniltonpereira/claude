#!/usr/bin/env bash
# Cadastra um provedor e imprime a chave de API (exibida uma única vez).
#   bash criar-provedor.sh "Provedor Exemplo Ltda" 12.345.678/0001-90
set -euo pipefail
cd "$(dirname "$0")"
[ $# -eq 2 ] || { echo "uso: $0 \"Razão Social\" CNPJ" >&2; exit 1; }
# Chama a API pela rede interna do Docker (não depende de DNS/HTTPS nem do filtro de IP do admin).
docker compose exec -T -e NOME="$1" -e CNPJ="$2" api python - <<'PY'
import json, os, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8000/v1/admin/providers",
    data=json.dumps({"name": os.environ["NOME"], "cnpj": os.environ["CNPJ"]}).encode(),
    headers={"Content-Type": "application/json", "X-Admin-Token": os.environ["ADMIN_TOKEN"]})
try:
    r = json.load(urllib.request.urlopen(req))
except urllib.error.HTTPError as e:
    raise SystemExit(f"Erro {e.code}: {e.read().decode()}")
print(f"Provedor nº {r['provider_id']} cadastrado.\nChave de API (guarde agora, não será exibida de novo):\n\n  {r['api_key']}\n")
PY
