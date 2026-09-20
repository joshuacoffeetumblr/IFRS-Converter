# Task 8 — Deployment

What it takes to run this in production, and what the system refuses to do if
you get it wrong.

The governing idea is that a misconfiguration which **stops** the process is an
outage, and the same misconfiguration which **starts cleanly** is an incident
nobody notices. This product holds client financial statements, so it is built
to fail the first way.

---

## 1. One command

```bash
cp .env.production.example .env      # then fill in every value
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose exec api alembic upgrade head
docker compose exec api python -m app.cli seed
```

`make up-prod` is the first two lines. Migrations and seeding are deliberately
separate: applying a migration is a decision, not a side effect of a restart.

---

## 2. What the overlay changes, and why

`docker-compose.yml` is a **development** environment — reload on change, source
mounted read-only, debug logging, and a database password published in this
repository. None of that is a defect there; all of it is a defect in
production. The overlay replaces every one of them.

| Base file | Overlay | Because |
|---|---|---|
| `POSTGRES_PASSWORD: ifrs18` | `${POSTGRES_PASSWORD:?}` | The base value is in this repository. Reaching production with it is not a weak password, it is a public one. |
| `ports: 5432:5432` | `ports: !reset []` | A mapped port on a host with a public interface is how a financial database ends up on the internet. |
| `./backend/app:/srv/app:ro` | `volumes: !override` | Compose **merges** volume lists rather than replacing them. Without `!override` the source mounts survive and the container runs the host's working tree instead of the image that was built and tested. |
| `uvicorn --reload` | plain `uvicorn` | Same reason. The image is the artefact. |
| `pg_isready -U ifrs18 -d ifrs18` | reads `$POSTGRES_USER` / `$POSTGRES_DB` | The literal healthcheck never goes healthy under other credentials, and the API waits on `service_healthy` — the stack would hang with nothing in the logs saying why. |
| `IFRS18_DEBUG: true` | `false` | A traceback from this service quotes financial data (spec §32). |

Every secret in the overlay is declared `${VAR:?}` — required, with no default —
so a missing one fails the `up` instead of silently falling back.

---

## 3. The API refuses to start on a dangerous configuration

With `IFRS18_ENVIRONMENT=production`, `Settings.check_production_ready()` runs
before the first request and raises on any of:

| Condition | Why it is fatal |
|---|---|
| `IFRS18_AUTH_SECRET_KEY` unset | The key is then generated per process — nobody set one on purpose, and sessions do not survive a deploy. |
| Signing key shorter than 32 characters | Too short to sign tokens with. |
| `IFRS18_DEBUG` on | Tracebacks from this service quote financial data (spec §32). |
| Database URL carrying `ifrs18:ifrs18` | The credentials published in this repository. |
| CORS origin `*` | Credentialed requests carry financial data. |
| CORS origin on plain `http://` | Uploads and exports must not travel in the clear. |
| CORS origin on `localhost` | How a production deployment ends up trusting a developer's laptop. |
| No CORS origins at all | The web app cannot call the API; better to say so than to serve 100% CORS failures. |

All of them are reported **at once**: an operator fixing a deployment should
learn everything that is wrong in one restart, not discover the next problem
after each fix.

---

## 4. The images

**API** — two stages. The builder has a compiler; the runtime does not. The
`dev` extra (pytest, ruff, mypy, reportlab) is never installed: a production
image should not carry the tools that test it. Runs as uid 10001, not root
(spec §31).

**Web** — the Next.js standalone output. Note that `next start` is
*incompatible* with `output: standalone` and serves a different bundle from the
one that was built; the image runs `node server.js`, and so does CI, so the
server the end-to-end tests hit is the artefact under test.

---

## 5. The AI assistant is optional, and off

```
IFRS18_AI_ENABLED=false     # the default
IFRS18_AI_API_KEY=
```

It requires **both** the switch and a key, plus the `ai` extra
(`pip install -e ".[ai]"`, already in the image). With none of them the product
works exactly as specified: every line a rule cannot decide reaches a qualified
person, which is where it was going anyway (spec §1). The assistant changes
only whether that person starts from a blank line or from a proposal they have
to check — it can never finalize a classification.

`app.adapters.ai.factory` returns **no** advisors when the SDK is absent, the
key is missing, or the switch is off. There is no half-enabled state.

---

## 6. What CI proves before a deploy

| Job | What it would otherwise let through |
|---|---|
| `backend` | lint, types, the full suite, migrations apply, and `alembic check` for un-migrated model drift |
| `frontend` | lint, types, build, and a production-dependency audit |
| `production-install` | A dependency the app imports but never declares. The other job installs `.[dev]`, which quietly supplied `email-validator` — without it `EmailStr` raises at import time and the production image could not import `app.main` **at all**. This job builds the wheel, installs it alone, and imports the thing from `/tmp` so nothing resolves out of the source tree. |
| `e2e` | A regression in the §34 flow that unit tests cannot see: Playwright against a live API and the standalone web build. |
| `docker` | An image that no longer builds. |

`production-install` also runs `python -m app.cli validate --ai` with the SDK
absent, because spec §1 says the product works without the assistant — so its
absence must be an ordinary configuration, not a crash.

---

## 7. Operational notes

**Uploads** live on a named volume and are pruned after
`IFRS18_UPLOAD_RETENTION_DAYS` (spec §32).

**Audit logs are append-only by database trigger** — `UPDATE` and `DELETE` are
refused at the database, which is what makes the audit trail worth anything
(spec §8). It has a consequence worth stating plainly: **a project with audit
rows cannot be hard-deleted**, and the upload retention policy does not cover
them. A retention or archival policy for audit data is a deliberate decision
that has not been made, not something to discover during a deletion request.

**The signing key is a rotation, not a redeploy.** Changing
`IFRS18_AUTH_SECRET_KEY` invalidates every session at once.

**Before trusting a deployment against real documents**, run the harness over a
real statement — see `docs/05-mvp-scope.md` §6. Nothing in this repository
proves the reader handles a document somebody else produced.
