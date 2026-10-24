#!/usr/bin/env bash
# Atualiza a Cerne na VPS: puxa o código, reconstrói a API, aplica migrações e sobe.
# Se a nova versão não ficar saudável em 90 s, volta para a imagem anterior.
#   bash deploy.sh             # atualiza a partir do git
#   bash deploy.sh --sem-pull  # só (re)constrói e sobe o que está no disco
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "Falta deploy/.env (copie de .env.example)." >&2; exit 1; }
DC="docker compose"
PERFIS=""; grep -q '^ATIVAR_SYNC=sim' .env 2>/dev/null && PERFIS="--profile sync"

if [ "${1:-}" != "--sem-pull" ]; then
  git -C .. pull --ff-only
fi
REV="$(git -C .. rev-parse --short HEAD 2>/dev/null || echo local)"

if docker image inspect cerne-api:latest >/dev/null 2>&1; then
  docker tag cerne-api:latest cerne-api:anterior
fi

echo "==> Construindo cerne-api ($REV)"
$DC build --pull api
$DC $PERFIS up -d --remove-orphans

echo "==> Aguardando a API ficar saudável"
for _ in $(seq 1 45); do
  st="$(docker inspect -f '{{.State.Health.Status}}' "$($DC ps -q api)" 2>/dev/null || echo starting)"
  [ "$st" = "healthy" ] && break
  sleep 2
done
if [ "$st" != "healthy" ]; then
  echo "!! API não ficou saudável ($st). Últimos logs:" >&2
  $DC logs --tail 50 api >&2
  if docker image inspect cerne-api:anterior >/dev/null 2>&1; then
    echo "!! Voltando para a imagem anterior." >&2
    docker tag cerne-api:anterior cerne-api:latest
    $DC $PERFIS up -d --no-build
  fi
  exit 1
fi
docker image prune -f >/dev/null
echo "==> Cerne $REV no ar: https://$(grep ^CERNE_DOMAIN= .env | cut -d= -f2)/healthz"
