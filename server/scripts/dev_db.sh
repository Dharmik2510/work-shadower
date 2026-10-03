#!/usr/bin/env bash
# Start (and on first run, initialise) a local Postgres 16 for development/tests.
# Usage: server/scripts/dev_db.sh [start|stop|status]
# Env overrides: PGDATA_DIR (default /home/claude/pgdata), PGPORT (default 5432)
set -euo pipefail
PGBIN=${PGBIN:-/usr/lib/postgresql/16/bin}
PGDATA_DIR=${PGDATA_DIR:-/home/claude/pgdata}
PGPORT=${PGPORT:-5432}
RUN_AS=${RUN_AS:-postgres}
CMD=${1:-start}

as_pg() {
  if [ "$(id -un)" = "$RUN_AS" ]; then "$@"; else runuser -u "$RUN_AS" -- "$@"; fi
}

case "$CMD" in
  stop)   as_pg "$PGBIN/pg_ctl" -D "$PGDATA_DIR" stop -m fast; exit 0 ;;
  status) as_pg "$PGBIN/pg_ctl" -D "$PGDATA_DIR" status; exit $? ;;
esac

if [ ! -s "$PGDATA_DIR/PG_VERSION" ]; then
  mkdir -p "$PGDATA_DIR"
  chown "$RUN_AS" "$PGDATA_DIR" 2>/dev/null || true
  as_pg "$PGBIN/initdb" -D "$PGDATA_DIR" -U postgres --auth=trust -E UTF8 >/dev/null
  echo "listen_addresses = 'localhost'" >> "$PGDATA_DIR/postgresql.conf"
  echo "port = $PGPORT" >> "$PGDATA_DIR/postgresql.conf"
  echo "unix_socket_directories = '/tmp'" >> "$PGDATA_DIR/postgresql.conf"
fi

if ! as_pg "$PGBIN/pg_ctl" -D "$PGDATA_DIR" status >/dev/null 2>&1; then
  as_pg "$PGBIN/pg_ctl" -D "$PGDATA_DIR" -l "$PGDATA_DIR/server.log" -w start >/dev/null
fi

for db in workshadower workshadower_test; do
  if ! as_pg "$PGBIN/psql" -h localhost -p "$PGPORT" -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1; then
    as_pg "$PGBIN/createdb" -h localhost -p "$PGPORT" -U postgres "$db"
  fi
done
echo "Postgres up on localhost:$PGPORT  (DATABASE_URL=postgresql://postgres@localhost:$PGPORT/workshadower)"
