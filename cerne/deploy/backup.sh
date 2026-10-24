#!/bin/sh
# Backup do Postgres com pg_dump (formato custom, comprimido) e retenção por dias.
#   backup.sh once   -> um backup agora
#   backup.sh loop   -> diário na hora BACKUP_HOUR (padrão 3h), usado pelo serviço "backup" do compose
# Restaurar: pg_restore --clean --if-exists -d cerne /backups/cerne-AAAAMMDD-HHMMSS.dump
set -eu
KEEP="${BACKUP_KEEP_DAYS:-14}"
DIR=/backups

run_once() {
  f="$DIR/cerne-$(date +%Y%m%d-%H%M%S).dump"
  pg_dump --format=custom --no-owner --file="$f.tmp" && mv "$f.tmp" "$f"
  echo "backup ok: $f ($(du -h "$f" | cut -f1))"
  find "$DIR" -name 'cerne-*.dump' -mtime +"$KEEP" -delete
}

case "${1:-once}" in
  once) run_once ;;
  loop)
    while true; do
      now=$(date +%s)
      next=$(date -d "$(date +%Y-%m-%d) ${BACKUP_HOUR:-3}:00:00" +%s 2>/dev/null || echo 0)
      [ "$next" -le "$now" ] && next=$((next + 86400))
      [ "$next" -le "$now" ] && next=$((now + 86400))   # date -d indisponível: a cada 24 h
      sleep $((next - now))
      run_once || echo "backup falhou" >&2
    done ;;
esac
