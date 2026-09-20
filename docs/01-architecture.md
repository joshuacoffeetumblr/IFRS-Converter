# Task 2 — Architecture Proposal

> Status: **proposal, awaiting approval.** No implementation code exists yet.

## 1. Design goal restated

The product is not "an AI that decides IFRS 18 treatment". It is a
**structuring and explanation engine**:

```
Financial Statements
  → Extraction        (deterministic, provenance-preserving)
  → Normalization     (dictionary + fuzzy, AI only as fallback)
  → Classification    (deterministic rules first, AI second, human last)
  → Reconstruction    (pure arithmetic, fully deterministic)
  → Reconciliation    (hard validation gate)
  → Impact Analysis   (pure arithmetic)
  → Export
```

Priority order, per spec §36: **Accuracy > Automation**,
**Traceability > Convenience**, **Explainability > Black-box AI**.

## 2. The three-layer decision model (spec §1)

This is the central architectural constraint and it dictates the module
boundaries.

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — Human Review                                        │
│   Final authority. Any decision can be overridden.            │
│   Every override writes an immutable audit record.            │
└──────────────────────────────────────────────────────────────┘
                     ▲ escalates                ▲ escalates
┌────────────────────┴──────────┐  ┌────────────┴─────────────┐
│ Layer 2 — AI Assistant        │  │ Layer 1 — Rule Engine     │
│   Only for accounts that      │  │   Deterministic, ordered, │
│   Layer 1 could not resolve.  │  │   versioned, cited rules. │
│   Structured JSON only.       │  │   Runs FIRST, always.     │
│   NEVER auto-final.           │  │   Produces the arithmetic.│
└───────────────────────────────┘  └───────────────────────────┘
```

**Critical inversion of the naive design.** A naive implementation asks the LLM
"is this operating?" for every line. That is both expensive and wrong, because
under IFRS 18 the **operating category is the residual category** — it is
defined as everything *not* classified into investing, financing, income tax, or
discontinued operations. Therefore:

> The rule engine's job is to positively identify **investing, financing, tax and
> discontinued** items. Everything that survives falls into **operating** by
> definition, not by guesswork.

This means the deterministic layer can cover the large majority of lines, and the
AI layer is reserved for genuinely ambiguous accounts (ambiguous Korean account
names, aggregated "기타수익" buckets, items whose treatment depends on facts about
the entity).

A third rule outcome is needed beyond match / no-match:

- `MATCH` — rule fires, category assigned.
- `NO_MATCH` — rule does not apply, try the next rule.
- `NEEDS_FACT` — the rule *would* fire but depends on an unconfirmed fact about
  the entity (typically a specified main business activity, spec §10). This
  **deterministically generates the user question** in Step 5 of the flow. The
  question in spec §5 ("이 회사에서 금융자산에 대한 투자가 주요 사업활동입니까?") is
  therefore produced by a rule, not by an LLM.

## 3. System topology

```
┌─────────────────────────────────────────────────────────┐
│  Next.js 15 (App Router) · TypeScript strict · Tailwind │
│  shadcn/ui · Recharts                                    │
│  ── presentation only: zero accounting arithmetic ──     │
└───────────────────────────┬─────────────────────────────┘
                            │ REST + JSON (OpenAPI-generated client)
┌───────────────────────────▼─────────────────────────────┐
│  FastAPI · Pydantic v2                                   │
│  routers/  ← HTTP concerns only (auth, status, DTO)      │
├──────────────────────────────────────────────────────────┤
│  services/  ← orchestration, transactions, audit writes  │
├──────────────────────────────────────────────────────────┤
│  domain/    ← PURE. No I/O, no ORM, no HTTP, no LLM.     │
│    ifrs18/rules      classification rule engine          │
│    ifrs18/statement  reconstruction + subtotals          │
│    ifrs18/impact     waterfall, KPI, bridge              │
│    ifrs18/validate   reconciliation identities           │
│    money             Decimal money type, sign convention │
├──────────────────────────────────────────────────────────┤
│  adapters/  ← replaceable I/O                            │
│    ingest/xlsx  ingest/csv  ingest/pdf                   │
│    llm/         (structured-output client)               │
│    export/xlsx  export/pdf                               │
│    storage/     (object storage for uploads)             │
├──────────────────────────────────────────────────────────┤
│  repositories/ ← SQLAlchemy 2.0, the only ORM users      │
└───────────────────────────┬──────────────────────────────┘
                            │
                  ┌─────────▼─────────┐   ┌────────────────┐
                  │  PostgreSQL 16    │   │ Object storage │
                  └───────────────────┘   └────────────────┘
```

### Why this split

- **`domain/` is pure and dependency-free.** The accounting logic is the part
  that must be provably correct (spec §30). A pure module is testable with
  plain `pytest` and no fixtures, database, or network. This is the single most
  important structural decision in the proposal.
- **The LLM lives in `adapters/`, not in `domain/`.** The domain calls an
  interface (`ClassificationAdvisor` protocol); the AI is one implementation.
  Tests substitute a stub. The accounting engine therefore never depends on a
  model being available, and the entire suite runs offline and deterministically.
- **Frontend has no arithmetic** (spec §13). Every number rendered — including
  every waterfall segment and every percentage — is computed server-side and
  transmitted as a value. The client formats; it never calculates. This makes
  the exported Excel and the on-screen dashboard provably identical.

## 4. Technology choices and rationale

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js (App Router) + TS strict | Spec §14; server components keep heavy tables off the client bundle |
| UI | Tailwind + shadcn/ui | Spec §14; unstyled primitives suit the dense "financial terminal" look (§20) better than an opinionated component library |
| Charts | **Recharts** | Spec §14 offers Recharts or ECharts. Recharts is chosen: the only non-trivial chart is the waterfall, which Recharts renders with a stacked-bar + transparent-base technique, and its React-native API keeps the click-through-to-rationale interaction (§5, §23) simple. ECharts would be preferred only if we later need very large datasets or canvas rendering. |
| Session | **httpOnly cookie, server-side fetches** | The bearer token is exchanged in a server action and stored in an httpOnly cookie; every API call runs on the server. `localStorage` would hand the token to any script that manages to run on the page, and this product holds unpublished financial statements (§31). |
| Mutations | **Server actions** | Keeps the token server-side without building a proxy route per endpoint, and lets the API's own problem documents — which name the rule that was broken — reach the user unaltered. |
| Backend | FastAPI + Pydantic v2 | Spec §14; Pydantic gives us one schema language for API DTOs *and* LLM structured output (§12) |
| ORM | **SQLAlchemy 2.0** (not SQLModel) | SQLModel couples the table model to the API model. We deliberately want them separate: the audit tables have fields that must never leave the backend, and the domain layer must not import ORM classes. SQLAlchemy 2.0's typed `Mapped[]` API gives full type hints without that coupling. |
| Migrations | Alembic | Standard companion to SQLAlchemy; the audit tables make schema history itself significant |
| Money | `decimal.Decimal` end to end; `NUMERIC(38,6)` in PG | Binary floats are disqualified for financial reconciliation. No `float` is permitted anywhere in `domain/`, enforced by a lint rule. |
| XLSX in | openpyxl | Spec §14; gives cell coordinates, which we require for provenance (§18) |
| CSV in | pandas | Spec §14 |
| PDF in | pdfplumber (primary), Camelot (fallback for ruled tables) | pdfplumber exposes word-level bounding boxes, which we need to store extraction provenance. Scanned/image PDFs are **out of MVP scope** — see `05-mvp-scope.md`. |
| XLSX out | openpyxl | Reuses the same library as ingest |
| PDF out | WeasyPrint (HTML→PDF) | Lets the export reuse the same layout vocabulary as the web UI |
| Tests | pytest + Playwright | Spec §14 |
| Local dev | Docker Compose (web, api, db) | Spec §27 Phase 1 |

## 5. Money and sign convention (a correctness-critical decision)

Ambiguous signs are the most common source of silent error in statement
restructuring tools. One convention is fixed for the whole system:

> **Every amount in `financial_statement_lines` and everything downstream is
> stored as its signed effect on profit or loss.** Income is positive. Expense is
> negative. Cost of sales of 70,000 is stored as `-70000`.

Consequences:
- Any category subtotal is a plain sum. No per-account sign lookup table.
- Profit before tax is the sum of every non-subtotal line. Always.
- The ingest adapter is responsible for converting presentation conventions
  (Korean statements often show expenses as unsigned positives in an expense
  column) into this convention, and records both the raw cell value and the
  normalized signed value so the transformation is auditable.

A second rule prevents the classic double-count:
> Extracted rows that are **subtotals** in the source (매출총이익, 영업이익, 법인세차감전순이익, …)
> are stored with `is_subtotal = true` and are **excluded from all summation**.
> They are retained solely as *reconciliation targets* — the figures we check our
> own arithmetic against.

## 6. Prompt-injection defense (spec §31)

Uploaded statements are untrusted input. The pipeline never sends a whole PDF to
a model (spec §17). Concretely:

1. The model is only ever given a **small structured payload** — an account name,
   an amount, the statement section it appeared in, and a short note excerpt —
   never raw document text in bulk.
2. All document-derived text is wrapped in explicit delimiters and labelled as
   untrusted data in the prompt, with a standing instruction that content inside
   it is data to classify, never instructions to follow.
3. The model has **no tools and no side effects**. Its only output is a JSON
   object validated against a fixed schema (§12). A response that does not
   validate is repaired or retried, then failed — it cannot cause an action.
4. Because the AI layer can never finalize a classification (§1), even a fully
   successful injection cannot change a reported number without a human
   review event recorded in the audit trail.

## 7. Validation as a hard gate (spec §19)

Reconciliation is not a warning banner bolted on at the end; it is a state
machine transition. A project cannot reach `FINALIZED` while a reconciliation
check fails.

Checks performed after reconstruction:

| Check | Identity | Tolerance |
|---|---|---|
| Category completeness | every non-subtotal line has exactly one final category | exact |
| Total invariance | Σ(all lines) after = Σ(all lines) before | exact |
| PBT invariance | IFRS 18 PBT = reported PBT | configurable, default ±1 presentation unit |
| Subtotal agreement | computed subtotals = reported subtotals where present | configurable |
| Operating bridge | reported OP + Σ(bridge items) = IFRS 18 OP | exact |

**Total invariance is exact and non-negotiable** (implemented Phase 6:
`app/domain/validation.py`; it takes no tolerance parameter at all, so it
cannot be relaxed by configuration). IFRS 18 changes *presentation*,
not measurement: reclassification moves an item between categories, so the sum of
all income and expenses cannot change. If it does, the software has a bug. This
invariant is the backbone of the test suite.

PBT invariance is given a tolerance only because the *source document* may itself
round (statements presented in 백만원 / billions). Any non-zero difference is
surfaced to the user with its magnitude, never silently absorbed.

On failure the UI shows "IFRS 18 reconstruction could not be reconciled." and the
impact figures are rendered in an explicitly degraded, marked state. Results are
never presented as normal (spec §19).

## 8. Request flow, end to end

```
POST /projects                      → project (DRAFT)
POST /projects/{id}/upload          → uploaded_file, virus/type/size checks
POST /projects/{id}/extract         → financial_statements + lines (+ provenance)
                                      reconciliation of extraction vs source subtotals
POST /projects/{id}/classify        → rule engine over every line
                                      ├ MATCH      → classification (method=RULE)
                                      ├ NEEDS_FACT → open question on business activity
                                      ├ NO_MATCH   → AI advisor → proposal (method=AI)
                                      └ residual   → OPERATING (method=RESIDUAL)
GET  /projects/{id}/classifications  → review queue, sorted by |impact on OP|
PATCH /classifications/{cid}         → human override → audit_log + user_review
POST /projects/{id}/finalize         → reconstruct + validate; FINALIZED or BLOCKED
GET  /projects/{id}/statement        → IFRS 18 statement with subtotals
GET  /projects/{id}/impact           → KPI before/after, waterfall, drivers
GET  /projects/{id}/export/{fmt}     → Excel / PDF
```

Extraction and classification are potentially slow, so both are modelled as
**asynchronous jobs** returning `202 Accepted` with a job handle; the project's
`status` field is the source of truth for the UI.

## 9. Review-queue ordering (a product decision)

The review screen sorts by **absolute impact on operating profit**, not by
confidence. A 0.62-confidence item worth ₩3m does not deserve attention before a
0.91-confidence item worth ₩80bn. Confidence determines *whether* review is
required; materiality determines *the order*. This is what makes the review step
finishable on a real statement with 200+ lines.

## 10. What is deliberately NOT in this architecture

- No accounting logic in React components (§13) — enforced by review and by the
  fact that the client receives only computed values.
- No automatic finalization of an AI judgement (§1), regardless of confidence
  (§11).
- No automatic determination of a company's main business activities from its
  industry code (§10) — always user-confirmed.
- No multi-tenant data sharing: every query is scoped by owning user/org at the
  repository layer (§31, §32).
