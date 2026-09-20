# Running it

Three ways. The first gives you a public URL; the third is the one verified
end to end in this repository.

---

## 1. Render — a real URL, one blueprint

`render.yaml` creates the API, the web app and a Postgres 16 database together.

1. Open **https://dashboard.render.com/blueprints** → **New Blueprint Instance**
2. Point it at this repository and branch. Render reads `render.yaml`.
3. It asks for two values it cannot know yet. Put anything in for now:
   - `IFRS18_CORS_ORIGINS` on **ifrs18-api**
   - `IFRS18_API_URL` on **ifrs18-web**
4. Once both services exist, Render has assigned their URLs. Go back and set:
   - `IFRS18_CORS_ORIGINS` = the **web** service's https URL
   - `IFRS18_API_URL` = the **api** service's https URL
5. Redeploy both. Open the web URL.

The API migrates and seeds itself on start, so there is nothing to run by hand.

> Free instances sleep after inactivity and take ~30s to wake; the free
> database expires after 30 days. Fine for a prototype, not for client work.
>
> Two things had to change before this could work, both found by trying it:
> the web app read its API URL from a `NEXT_PUBLIC_` variable, which Next
> inlines at **build** time — before the API has a URL — so a container could
> only ever talk to `localhost:8000`; and a managed database hands out
> `postgresql://` with no driver, which neither the async engine nor Alembic
> can use.

---

## 2. Docker — one command

```bash
cp .env.example .env
docker compose up --build
```

Then, once the API container is healthy:

```bash
docker compose exec api alembic upgrade head
docker compose exec api python -m app.cli seed
```

Open **http://localhost:3000**.

> This path was **blocked until 2026-09-20**: `IFRS18_CORS_ORIGINS` is a
> comma-separated string, pydantic-settings decoded list-shaped fields as JSON
> in the environment source before any validator ran, and the process died at
> import. Fixed, and covered by tests that set the variable rather than
> constructing `Settings` directly. It has not yet been run against a real
> Docker daemon, because the development sandbox has none — CI builds both
> images but does not `compose up`.

---

## 3. Without Docker — verified

Needs PostgreSQL 16, Python 3.12, Node 22.

```bash
# database
createdb ifrs18

# backend
cd backend
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
export IFRS18_DATABASE_URL="postgresql+asyncpg://$USER@localhost:5432/ifrs18"
export IFRS18_CORS_ORIGINS="http://127.0.0.1:3000,http://localhost:3000"
export IFRS18_AUTH_SECRET_KEY="$(openssl rand -base64 36)"
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli seed
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# frontend
cd ../frontend
npm ci
npm run build
mkdir -p .next/standalone/.next
cp -r .next/static .next/standalone/.next/      # note the trailing slash
IFRS18_API_URL=http://127.0.0.1:8000 node .next/standalone/server.js
```

Open **http://127.0.0.1:3000**.

> `node .next/standalone/server.js`, not `npm start`. `next start` is
> incompatible with `output: standalone` and serves a different bundle from the
> one that was built.

---

## Checking a file without running anything

No database, no network, no browser. Use this on a document that may not be
uploaded anywhere.

```bash
cd backend
make -C .. validate f=손익계산서.xlsx    # or .csv, or .pdf
```

It reports whether the document's own subtotals reproduce, which captions the
dictionary did not recognise, which rules fired, what a reviewer would be
asked, and whether the validation gate opens. It exits non-zero when the file
would not produce a shippable result.

---

## What to expect on a real filing

**The gate will stay shut, and that is the product working.** IFRS 18 turns on
facts that are not in the statement — which item gave rise to a foreign
exchange difference (B65), which risk a derivative manages (B72), whether
investing in assets is a main business activity (¶49-50). Nothing infers those
from an industry code. They appear as questions in the review queue, and
finalization is refused until somebody answers them.

Aggregate captions — 기타수익, 기타비용, 금융수익, 금융비용 — stay
UNCLASSIFIED until they are decomposed, because those are exactly the captions
IFRS 18 exists to look inside. Their breakdown is in the filing's own notes.

**Set the presentation unit to match the document.** A DART 재무제표 is printed
in 원; the project form defaults to 백만원. When the document states its unit
the document wins, but when it states none the project's setting is used.

---

## Production

`docs/08-deployment.md`. With `IFRS18_ENVIRONMENT=production` the API refuses
to start on an unset or short signing key, debug mode, this repository's own
database credentials, or a CORS origin that is a wildcard, plaintext, or
localhost — all reported at once.
