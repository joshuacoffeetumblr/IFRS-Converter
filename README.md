# IFRS 18 Impact Analyzer

Restructures a company's reported income statement under **IFRS 18
*Presentation and Disclosure in Financial Statements*** and shows, with a full
audit trail, how and why operating profit changes.

> **This analysis is an IFRS 18 classification and impact analysis tool. It does
> not constitute accounting advice or an authoritative determination of IFRS
> compliance.**

---

## Status: the MVP flow runs end to end

Phases 1–9 are complete: repository and tooling, the database schema,
XBRL + XLSX + CSV + PDF extraction with line-level provenance and reconciliation
against the source's own subtotals, account normalization against a catalog of 34 canonical
accounts and 198 synonyms, the IFRS 18 classification engine, statement reconstruction behind a
reconciliation gate, impact analysis, and Excel export.

The analytical core is complete and driven end to end by
`app/services/pipeline.py`.

The REST API now covers the whole chain: authentication and projects, file
upload and extraction, classification and human review, finalization behind
the reconciliation gate, the IFRS 18 statement, the impact analysis and the
Excel export — plus the rule set, the account dictionary and the audit trail,
read back. The engine proposes and a person decides: a re-run never discards a
human decision, an override without a reason is refused, and an analysis that
did not reconcile is never served as a normal result. See
[`docs/03-api-specification.md`](docs/03-api-specification.md) for what is
built and what is not.

The screens are built on top of it: projects, upload and extraction, the review
queue with the questions the rules raised, the IFRS 18 statement, the impact
analysis with its waterfall, and the audit trail. Playwright drives the whole
flow against a live API. Two decisions shape the client: the session token
lives in an httpOnly cookie and every API call runs on the server, so no script
in the browser can reach it; and every figure crosses as a string and is
formatted, never computed, in the browser — the accounting stays in one place.

```bash
make demo   # runs the whole chain over the fixture and writes an .xlsx
```

On the synthetic fixture the full chain runs end to end:

| | Before | After | Change |
|---|---|---|---|
| Revenue | 1,000,000 | 1,000,000 | 0 |
| **Operating profit** | **120,000** | **126,000** | **+6,000 (+5.00%)** |
| Operating margin | 12.00% | 12.60% | +60bp |
| Profit before financing and income taxes | — | 136,000 | *new under IFRS 18* |
| Profit before tax | 122,000 | 122,000 | **0** |
| Profit for the period | 95,160 | 95,160 | **0** |

The change comes entirely from decomposing 기타수익 and 기타비용 — a disposal
gain and loss move into operating, and IFRS 18 B65 brings the FX on trade
receivables with them. Left aggregated, the change would have read as zero.

> **Citations are `VERIFIED_SECONDARY`.** Every rule names the IFRS 18
> paragraph it implements, confirmed against IFRS Foundation and Big 4
> publications — not against the issued text of the standard, which this
> environment cannot reach. Two rules that fire on ordinary non-financial
> corporates have conditions still to be confirmed, so **every match on them is
> routed to human review**. See
> [`docs/07-ifrs18-source-verification.md`](docs/07-ifrs18-source-verification.md).

The three grid adapters share one extraction pipeline
(`app/adapters/ingest/grid.py`), so the format is transport only — a test
asserts XLSX, CSV and PDF produce identical figures, identical coverage and the
same verdict from the same statement.

A PDF with no text layer is **refused**, not OCR'd: OCR misreads a digit
silently, which is the one failure this product cannot have.

**XBRL does not go through that pipeline, because a filing is not a grid.**
Every grid reader spends its effort guessing: which column holds the figures,
which row is a subtotal, whether an unsigned number is a deduction, what unit
the page is in. Each of those guesses has been a defect at least once. An XBRL
instance states all of it — the concept says whether a line is a subtotal and
whether it is a deduction, the context says which period and whether it is
consolidated, and a concept means the same thing in a Korean filing and an
English one. So `app/adapters/ingest/xbrl.py` reads the taxonomy directly and
guesses at nothing.

It also reads the notes. Where a filing breaks a caption down — 금융수익 into
interest, FX and derivative gains — the components replace the caption, but
**only where they add up to it exactly**. A breakdown that does not reconcile is
not one we have understood, and those are the lines IFRS 18 turns on: ¶49-50,
B65 and B72 each classify a different part of 금융수익, so an approximate split
would be worse than none. Which statement is read comes from the project, since
a filing carries every basis and every period at once; a period it does not
report is an error naming the ones it does, never the nearest match.

> **Validated against a real filing on 2026-09-20** — Samsung Electronics'
> 2026 half-year DART XBRL. All five §17 reconciliation checks agree to the
> won, on figures the filing prints entirely unsigned; dictionary coverage
> 100%. The gate correctly stays shut on four aggregate captions pending note
> decomposition, which is what IFRS 18 exists to look inside. Three defects
> were found getting there, all one root cause: every caption table in the
> ingest layer was Korean-only. See `docs/05-mvp-scope.md` §6.0.
>
> **Those four captions are now read from the filing itself** (2026-09-21).
> Reading the XBRL instance directly takes the same statement from 9 detail
> lines to 18, all four note breakdowns reconciling to the won, with no person
> transcribing anything. See `docs/05-mvp-scope.md` §6.1.

> **Test fixtures are synthetic.** They imitate the shape of a K-IFRS
> 손익계산서 but are not drawn from a real filing, so they validate the parser,
> not the account dictionary or the rule set. A statement we wrote cannot fail
> its own arithmetic in an interesting way. Run `make fixture` to write them
> out and inspect them.
>
> That gap closes with a real document, not with more tests — so the command
> that consumes one is built and waiting:
>
> ```bash
> make validate f=손익계산서.xlsx      # or .csv, .pdf, .xbrl
> make validate f=filing.xbrl args="--from 2026-01-01 --to 2026-06-30"
> ```
>
> No database, no network, so it runs on a file that may not be uploaded
> anywhere. It reports whether the document's own subtotals reproduce, which
> captions the dictionary did not recognise, which rules fired, what a reviewer
> would be asked, and whether the validation gate opens — and exits non-zero
> when the file would not produce a shippable result. The expected first
> outcome on a real filing is a list of unrecognised captions, not a pass.
> That list is the work item.

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
| `make seed` | Load the account catalog and rule set into the database |
| `make demo` | Run the pipeline over the fixture and write an Excel export |

API docs at `http://localhost:8000/docs`.

### Verification

Verified on 2026-09-19 against a live PostgreSQL 16 and both servers running:

- `GET /api/health` → `200`
- `GET /api/ready` → `200` with a database, `503 degraded` without
- `alembic upgrade head` applies to a **completely empty** database (it creates
  the `citext` extension itself), `alembic downgrade base` reverses it, and
  `alembic check` reports no drift from the models
- The `audit_logs` append-only trigger rejects both UPDATE and DELETE
- The landing page renders the §24 disclaimer **fetched from the API**, not a
  local copy
- Backend: 1,170 tests pass, `ruff` clean, `mypy --strict` clean
- Total invariance is exact and unconfigurable: reclassification cannot change
  the sum of all income and expenses
- The waterfall is derived from the same per-line movement the gate checks, so
  it cannot disagree with the statement
- 100% of detail lines normalize **without AI** on both the canonical fixture
  and a "messy" one whose captions appear nowhere in the catalog verbatim
  (Phase 4 target was 90%)
- Every enum-backed CHECK constraint is compared against its Python enum,
  because Alembic autogenerate does not diff CheckConstraints
- All three Korean sign conventions extract to identical figures, and an
  unsigned statement is resolved only because its own subtotals then reconcile
- CSV decodes UTF-8, UTF-8-with-BOM, CP949 and EUC-KR, and detects `,` `;`
  tab and `|` delimiters
- An upload's format is decided by its leading bytes, its size is enforced
  mid-stream, a workbook that expands like a zip bomb is refused, and a
  rejected upload leaves nothing on disk
- A ZIP that is not a workbook — a DART filing bundle, most likely — is refused
  with the name of the file inside to upload instead, rather than dying inside
  the workbook reader on a missing archive member
- An XBRL filing's taxonomy concepts are matched by **namespace**, not by the
  prefix the filer happened to declare, and a note breakdown is used only where
  it reconciles to the caption it explains
- Every failure reaches the client as a problem document, including an
  unreachable database — which is a `503` naming the dependency rather than a
  bare `Internal Server Error` that reads as a rejected password
- A failed extraction is recorded rather than rolled back, so a project whose
  statement could not be read never looks untouched
- A note column printed as bare numbers — how filings actually print it — is
  not mistaken for the current period, so the subtotals that carry no note
  survive and the statement can be checked against its own arithmetic
- Won-scale figures survive exactly: 15-digit integers with no presentation
  unit, which is where a reader that goes through binary floating point starts
  returning numbers nobody wrote
- An English-captioned filing is read, not refused: subtotals are recognised
  exactly (so `Profit from disposal of investments` never becomes one), and a
  parenthetical qualifier never turns income into a deduction — `Share of
  profit (loss) of associates` is a gain
- An AI-suggested business activity cannot unblock a rule: unconfirmed reads as
  *unknown* to the engine, and the database refuses to store it as confirmed
- Answering a question re-runs classification immediately, and `NOT_SURE` is
  stored while still blocking — being asked and not knowing is not a fact
- Every question in the review screen is raised by a rule and carries its
  wording, its help text and the amount that turns on it, from a data file
  checked against the rule set at load time
- Finalization answers 409 with every failing check, and records the failure
  rather than discarding it — a project whose reconciliation failed never
  looks untouched
- An export of an unreconciled analysis has to be asked for explicitly and
  comes back watermarked on every sheet
- The rule set, the dictionary and the active thresholds are served from the
  same files the engine runs on, unauthenticated, so the logic behind a number
  can always be read
- A hostile caption cannot widen what the assistant may answer: the response is
  constrained to an enum built from the domain's own categories, and a
  suggestion that still fails validation after one repair is discarded — which
  sends the line to a person, where it was going anyway
- Nothing from an AI exchange reaches the logs: what is logged is its shape,
  never a caption, a figure, or the model's words
- The advisor's request is checked against the **installed** SDK's own
  signature and typed parameters, because a rejected request would otherwise
  look exactly like a model with no opinion
- CI installs the built wheel with no dev extra and imports the application, so
  a dependency the app needs but never declares fails there instead of in
  production
- Frontend: `eslint` clean, `tsc --noEmit` clean, production build succeeds,
  3 Playwright tests pass, `npm audit` reports 0 vulnerabilities

Docker image builds are exercised in CI; they could not be run locally because
the development sandbox has no Docker daemon. The production install *was*
verified locally, by building the wheel, installing it into an empty
environment with no dev extra and importing the application — which is how
`email-validator` was found missing from the declared dependencies. Without it
`EmailStr` raises at import time and the production image could not start at
all.

Running it in production: [`docs/08-deployment.md`](docs/08-deployment.md).

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
openpyxl · pdfplumber — pytest · Playwright — Docker Compose

Rationale for each choice is in [`docs/01-architecture.md`](docs/01-architecture.md) §4.
