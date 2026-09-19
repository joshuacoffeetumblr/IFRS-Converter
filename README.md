# IFRS 18 Impact Analyzer

Restructures a company's reported income statement under **IFRS 18
*Presentation and Disclosure in Financial Statements*** and shows, with a full
audit trail, how and why operating profit changes.

> **This analysis is an IFRS 18 classification and impact analysis tool. It does
> not constitute accounting advice or an authoritative determination of IFRS
> compliance.**

---

## Status: design phase — no application code yet

Per the project specification (§26, §35), design precedes implementation.
The repository currently contains design documents only. Implementation of
Phase 1 begins after these are approved.

| Doc | Task | Contents |
|---|---|---|
| [`docs/00-repository-audit.md`](docs/00-repository-audit.md) | 1 | What was in the repo before any work started |
| [`docs/01-architecture.md`](docs/01-architecture.md) | 2 | Layering, technology choices, sign convention, validation gate, injection defense |
| [`docs/02-erd.md`](docs/02-erd.md) | 3 | Full schema, state machine, schema-level decisions |
| [`docs/03-api-specification.md`](docs/03-api-specification.md) | 4 | REST contract, payload shapes, deviations from spec §15 |
| [`docs/04-classification-engine.md`](docs/04-classification-engine.md) | 5 | Domain model, rule DSL, draft rule set, confidence model, test vectors |
| [`docs/05-mvp-scope.md`](docs/05-mvp-scope.md) | 6 | **Scope for approval**, phase plan, definition of done |
| [`docs/06-open-accounting-questions.md`](docs/06-open-accounting-questions.md) | — | **Q1–Q7: accounting uncertainties requiring your decision** |

**Start with `docs/05-mvp-scope.md` and `docs/06-open-accounting-questions.md`.**

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

## Proposed stack

Next.js · TypeScript · Tailwind · shadcn/ui · Recharts —
FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · PostgreSQL 16 —
openpyxl · pandas · pdfplumber — pytest · Playwright — Docker Compose

Rationale for each choice is in [`docs/01-architecture.md`](docs/01-architecture.md) §4.
