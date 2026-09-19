# IFRS 18 Impact Analyzer

Restructures a company's reported income statement under **IFRS 18
*Presentation and Disclosure in Financial Statements*** and shows, with a full
audit trail, how and why operating profit changes.

> **This analysis is an IFRS 18 classification and impact analysis tool. It does
> not constitute accounting advice or an authoritative determination of IFRS
> compliance.**

---

## Status: Phase 1 — foundation

Design was approved on 2026-09-19 and Phase 1 (repository, tooling, skeleton,
CI) is complete. No accounting logic exists yet; that arrives in Phases 5–7.

IFRS 18 citations were verified on 2026-09-19 against IFRS Foundation and Big 4
sources — see
[`docs/07-ifrs18-source-verification.md`](docs/07-ifrs18-source-verification.md).
A handful of handling decisions remain before the rule set is seeded in Phase 5;
see [`docs/06-open-accounting-questions.md`](docs/06-open-accounting-questions.md).

| Doc | Task | Contents |
|---|---|---|
| [`docs/00-repository-audit.md`](docs/00-repository-audit.md) | 1 | What was in the repo before any work started |
| [`docs/01-architecture.md`](docs/01-architecture.md) | 2 | Layering, technology choices, sign convention, validation gate, injection defense |
| [`docs/02-erd.md`](docs/02-erd.md) | 3 | Full schema, state machine, schema-level decisions |
| [`docs/03-api-specification.md`](docs/03-api-specification.md) | 4 | REST contract, payload shapes, deviations from spec §15 |
| [`docs/04-classification-engine.md`](docs/04-classification-engine.md) | 5 | Domain model, rule DSL, draft rule set, confidence model, test vectors |
| [`docs/05-mvp-scope.md`](docs/05-mvp-scope.md) | 6 | Approved scope, phase plan, definition of done |
| [`docs/06-open-accounting-questions.md`](docs/06-open-accounting-questions.md) | — | Q1, Q2, Q7 resolved; Q3–Q5 researched; **Q6 open** |
| [`docs/07-ifrs18-source-verification.md`](docs/07-ifrs18-source-verification.md) | — | Verified IFRS 18 citations (B65, B72, 73, 24, B30) and the corrected rule set |

**Start with `docs/05-mvp-scope.md` and `docs/06-open-accounting-questions.md`.**

---

## Running it

```bash
cp .env.example .env
make up                 # db + api + web via Docker Compose
```

Or without Docker:

```bash
make setup              # backend venv + frontend deps
make migrate            # apply migrations
cd backend  && .venv/bin/uvicorn app.main:app --reload   # http://localhost:8000
cd frontend && npm run dev                               # http://localhost:3000
```

| Target | What it does |
|---|---|
| `make check` | Everything CI runs: lint, types, tests |
| `make test` | Backend test suite |
| `make e2e` | Playwright tests (stack must be running) |
| `make revision m="..."` | Autogenerate a migration |

API docs at `http://localhost:8000/docs`.

### Phase 1 verification

Verified on 2026-09-19 against a live PostgreSQL 16 and both servers running:

- `GET /api/health` → `200`
- `GET /api/ready` → `200` with a database, `503 degraded` without
- `alembic upgrade head` applies; `alembic check` reports no drift
- The landing page renders the §24 disclaimer **fetched from the API**, not a
  local copy
- Backend: 11 tests pass, `ruff` clean, `mypy --strict` clean
- Frontend: `eslint` clean, `tsc --noEmit` clean, production build succeeds,
  3 Playwright tests pass, `npm audit` reports 0 vulnerabilities

Docker image builds are exercised in CI; they could not be run locally because
the development sandbox has no Docker daemon.

---

## The core idea

IFRS 18 judgement is **not** delegated to an LLM. Three layers, in order:

1. **Deterministic rule engine** — cited, versioned rules decide what they can.
   Because IFRS 18 makes *operating* the residual category, the rules only need
   to positively identify investing, financing, tax and discontinued items;
   everything else is operating by definition of the standard.
2. **AI assistant** — invoked only for genuinely ambiguous accounts, returns
   strict JSON, and **can never finalize a classification**.
3. **Human review** — final authority. Every override is recorded.

Priorities: **Accuracy > Automation. Traceability > Convenience.
Explainability > Black-box AI.**

## Pipeline

```
Upload → Extract (with cell-level provenance)
       → Normalize accounts
       → Classify (rules → questions → AI → human)
       → Reconstruct IFRS 18 statement
       → Reconcile  ← hard gate: no reconciliation, no finalized result
       → Impact analysis (KPIs, waterfall, drivers)
       → Export
```

## Stack

Next.js 16 · TypeScript (strict) · Tailwind · Recharts —
FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · PostgreSQL 16 —
openpyxl · pandas · pdfplumber *(Phase 3)* — pytest · Playwright — Docker Compose

Rationale for each choice is in [`docs/01-architecture.md`](docs/01-architecture.md) §4.
