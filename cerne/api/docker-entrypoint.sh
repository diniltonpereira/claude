#!/bin/sh
# api    -> aplica migrações e sobe o servidor
# sync   -> roda o job de sincronização uma vez (argumentos extras repassados)
# worker -> roda o sync de todos os provedores em laço (SYNC_INTERVAL_HOURS, padrão 24)
set -e
case "$1" in
  api)
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
      --workers "${WEB_CONCURRENCY:-2}" --proxy-headers --forwarded-allow-ips='*' --no-server-header --no-access-log
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  sync)
    shift
    exec python -m app.sync "$@"
    ;;
  worker)
    while true; do
      python -m app.sync --all || echo "sync terminou com erro (ver acima)"
      sleep $(( ${SYNC_INTERVAL_HOURS:-24} * 3600 ))
    done
    ;;
  *)
    exec "$@"
    ;;
esac
