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
attempt=1
until alembic upgrade head; do
  if [ "$attempt" -ge 10 ]; then
    echo "database never became reachable after $attempt attempts; refusing to start" >&2
    exit 1
  fi
  echo "database not ready (attempt $attempt), retrying in 5s"
  attempt=$((attempt + 1))
  sleep 5
done

# Idempotent: it diffs the catalog and the rule set rather than reinserting
# them, so a restart seeds nothing and changes nothing.
python -m app.cli seed

# `exec` so uvicorn becomes PID 1 and receives the platform's stop signal
# directly, instead of a shell swallowing it and forcing a kill.
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
