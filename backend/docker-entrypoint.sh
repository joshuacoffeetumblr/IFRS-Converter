#!/bin/sh
# What the container does before it serves anything.
#
# This lives in a file rather than in a platform's `command:` field because
# quoting rules differ between platforms and are not visible until a deploy
# fails. Render wraps its `dockerCommand` in a shell of its own, so a command
# that wrapped itself in `sh -c` had the whole inner string taken as one
# command name — `sh: 1: alembic upgrade head && …: not found`. A file has no
# such ambiguity, and it can be run and tested exactly as the container runs it.
set -eu

PORT="${PORT:-8000}"

# A blueprint creates the database and the services together, so on a first
# deploy Postgres may not be accepting connections yet. Retry rather than
# exiting — an exit here is a failed deploy with a perfectly good build.
# 30 × 5s. A managed database can take a couple of minutes to finish
# provisioning on a first deploy, and giving up early looks identical to a
# broken build.
attempt=1
max_attempts=30
until alembic upgrade head 2>/tmp/alembic.err; do
  tail -3 /tmp/alembic.err >&2 || true
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "" >&2
    echo "The database never became reachable. Refusing to start." >&2
    if grep -q "failed to resolve host\|Name or service not known" /tmp/alembic.err 2>/dev/null; then
      echo "" >&2
      echo "The hostname did not resolve at all, which usually means the" >&2
      echo "database is in a DIFFERENT REGION from this service. A managed" >&2
      echo "database's internal hostname only resolves from inside its own" >&2
      echo "region, and neither a database nor a service can be moved after" >&2
      echo "it is created — both have to be recreated in the same region." >&2
      echo "Check the region shown on the database and on this service." >&2
    fi
    exit 1
  fi
  echo "database not ready (attempt $attempt of $max_attempts), retrying in 5s"
  attempt=$((attempt + 1))
  sleep 5
done

# Idempotent: it diffs the catalog and the rule set rather than reinserting
# them, so a restart seeds nothing and changes nothing.
python -m app.cli seed

# `exec` so uvicorn becomes PID 1 and receives the platform's stop signal
# directly, instead of a shell swallowing it and forcing a kill.
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
