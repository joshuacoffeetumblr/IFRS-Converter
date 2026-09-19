# Task 6 — MVP Scope (redefined)

> **Status: ✅ APPROVED 2026-09-19.** Phase 1 implementation is under way.
>
> Scope approved as written: XLSX first, non-financial Korean corporates,
> single period, single currency, P&L only, PDF sequenced last.

## 1. The one-sentence MVP

> A signed-in user uploads an **XLSX income statement**, the system extracts it
> with cell-level provenance, classifies every line into IFRS 18 categories using
> a **cited deterministic rule set** with AI assistance only where rules cannot
> decide, asks the user the **specific factual questions** the rules depend on,
> lets the user override any classification, reconstructs the statement, **proves
> it reconciles**, and shows exactly **why operating profit changed** — with an
> Excel export and a complete audit trail.

Spec §34's twelve success criteria map onto this one-for-one. Nothing in §34 is
dropped.

## 2. Narrowing decisions, and why

Spec §33 says to secure high accuracy in a narrow scope first. Four narrowings
carry that instruction further than §33 does:

### 2.1 XLSX + CSV first; PDF moved to end of MVP

Spec §34 defines success in terms of **XLSX**. PDF extraction is the single
largest source of both engineering cost and silent data error, and an incorrectly
extracted number defeats every downstream guarantee. PDF stays in the MVP but is
sequenced **last** (Phase 3c) so it can be cut without touching §34.

**Scanned / image-only PDFs are out of MVP entirely** — OCR of Korean financial
tables is its own project. The uploader detects and rejects them with a clear
message rather than producing a plausible-looking wrong extraction.

### 2.2 Non-financial commercial entities only

Banks, insurers and securities firms are where IFRS 18's specified main business
activity provisions bite hardest, and where getting it wrong is most damaging.
They are also where my confidence in the rule set is lowest (Q3). MVP targets
**non-financial Korean corporates**. The SMBA machinery is still built — it is
architecturally central — but the seeded rule set is validated only against
non-financial statements, and the UI states the limitation.

### 2.3 Single period, single entity, single statement

One income statement, one period, one basis (consolidated *or* separate). The
schema supports comparatives and multiple statements (T12), but the MVP UI
restructures one. Multi-period trend analysis is Phase 10+.

### 2.4 KRW, single currency

`presentation_currency` is stored and displayed but no translation is performed.
Multi-currency groups are out of scope.

## 3. In scope / out of scope

### In scope

| # | Capability | Spec ref |
|---|---|---|
| 1 | Email+password auth, project ownership isolation | §31 |
| 2 | Upload XLSX/CSV with type, size, hash and scan checks | §3, §31 |
| 3 | Income-statement extraction with cell-level provenance | §17, §18 |
| 4 | Extraction reconciliation against the source's own subtotals | §17 |
| 5 | Manual correction of extracted lines | — (necessary) |
| 6 | Account normalization: exact → synonym → fuzzy → AI fallback | §4 |
| 7 | Deterministic rule engine with cited, versioned rules | §1, §25 |
| 8 | `NEEDS_FACT` → user questions on main business activities | §5, §10 |
| 9 | AI advisor with strict JSON schema, retry/repair, never final | §1, §12 |
| 10 | Review queue sorted by materiality; override with reason | §1, §7 |
| 11 | Statement reconstruction with IFRS 18 subtotals | §6 |
| 12 | Reconciliation gate blocking finalization | §19 |
| 13 | Impact dashboard: KPIs, waterfall, top reclassifications, drivers | §5, §6, §22, §23 |
| 14 | Click-through from any number to its source cell and rationale | §7, §23 |
| 15 | Excel export including the audit trail | §8 |
| 16 | Full audit trail on every classification and override | §8 |
| 17 | Disclaimer surfaced in UI **and** in every export | §24 |

### Out of scope for MVP (and stated as limitations in-product)

| Excluded | Why | Spec ref |
|---|---|---|
| Scanned/OCR PDFs | Separate problem; high silent-error risk | §33 |
| DART / XBRL ingestion | §3 lists as future; §33 excludes | §3, §33 |
| Banks, insurers, securities firms | Lowest rule confidence; IFRS 18.73 prohibits a subtotal we present, so such projects are **blocked at finalization** (Q3) | §33 |
| MPM (management-defined performance measure) disclosure | Prominent IFRS 18 requirement, deliberately deferred — see Q7 | — |
| OCI restructuring, aggregation/disaggregation requirements | P&L only — see Q7 | — |
| Balance sheet / cash flow restructuring | P&L only | §2 |
| Multi-currency translation | — | — |
| PDF export | Sequenced after Excel; Excel satisfies §34 | §34 |
| Non-IFRS GAAPs, automatic audit opinion, automatic policy determination | Explicitly excluded | §33 |
| Team collaboration, comments, approval workflow | Single-user MVP | — |

## 4. Revised phase plan

Spec §27's phases are kept, with sequencing adjusted for risk. **The riskiest
assumption is not the plumbing — it is whether the rule set produces defensible
classifications on a real Korean statement.** So Phase 5 is validated against a
real statement as early as possible, using a hand-built fixture rather than
waiting for PDF ingestion.

| Phase | Content | Exit criterion |
|---|---|---|
| **0** | These design docs; Q1–Q7 answered | ✅ **approved 2026-09-19** (Q1, Q2, Q7 resolved; Q3–Q6 open, Phase 5 only) |
| **1** | Monorepo, Docker Compose, FastAPI + Next.js skeleton, Postgres, Alembic, ruff/mypy/eslint, pytest, CI | `docker compose up` works; CI green on an empty test suite |
| **2** | Full schema + migrations; domain money type; sign convention; repositories | Schema matches `02-erd.md`; round-trip tests pass |
| **3a** | XLSX ingest with provenance + extraction reconciliation | ✅ done 2026-09-19 against synthetic fixtures; a **real** statement is still outstanding |
| **3b** | CSV ingest | ✅ done 2026-09-19 — shares one pipeline with XLSX; Korean encodings (CP949/EUC-KR/BOM) handled |
| **3c** | PDF ingest (text-based only) | *Cuttable without affecting §34* |
| **4** | Normalization dictionary (Korean + English), synonyms, fuzzy matching, **note-based decomposition of aggregate captions (Q5)** | ✅ done 2026-09-19 — 100% without AI on both the canonical and the messy fixture; decomposition refuses components that do not sum to the caption |
| **5** | **Rule engine, rule seed data with verified citations, company- and line-scoped `NEEDS_FACT` (Q4), AI advisor** | ✅ done 2026-09-19 — 10 cited rules, all `VERIFIED_SECONDARY`; the two with unconfirmed conditions force human review |
| **6** | Reconstruction, subtotals, reconciliation gate | ✅ done 2026-09-19 — total invariance exact on every fixture; the gate blocks on an unclassified line, an unanswered question, or a subtotal IFRS 18.73 forbids |
| **7** | Impact: KPIs, waterfall, drivers | ✅ done 2026-09-19 — the waterfall is built from the movement the gate checks, so it sums to the delta by construction; measures IFRS 18 introduced report no "before" rather than a false zero |
| **8** | Excel export (+ PDF if time) | ✅ done 2026-09-19 — six sheets incl. audit trail and reconciliation; a figure that would lose precision fails the export rather than shipping altered; unreconciled files are watermarked on every sheet. **PDF export not built** — Excel satisfies §34. |
| **9** | UX refinement, empty/loading/error states, responsive | Playwright covers the full §4 flow |

**Phase 0 closed 2026-09-19.** Q1, Q2 and Q7 are resolved, so schema and
impact-screen work is unblocked. Q3–Q6 remain open and block Phase 5 rule
seeding only; Phases 1–4 proceed.

## 5. Definition of done for the MVP

A single end-to-end Playwright test executing spec §34 verbatim:

1. Upload `fixtures/kr_manufacturer_2025.xlsx`
2. Extraction produces the expected line count and reconciles to the source
3. ≥90% of lines normalize without AI
4. Every line receives a classification with a recorded method
5. The SMBA question appears and blocks finalization
6. Answering it resolves the affected lines
7. One classification is overridden; the audit trail records it
8. Finalize succeeds; `reconciliation_status == PASSED`
9. Operating profit before/after/change/change% match hand-computed values
10. The waterfall sums exactly to the change
11. Excel export contains statement, impact, account detail and audit trail
12. Every classification has a non-empty audit record

If that test passes, the MVP is done.

## 6. Outstanding inputs needed

1. ~~Approve or amend this scope~~ — ✅ approved 2026-09-19.
2. ~~Answer Q3–Q6~~ — ✅ all resolved 2026-09-19.
3. **Confirm access to the issued text of IFRS 18** (spec §25). Citations were
   verified on 2026-09-19 against IFRS Foundation and Big 4 sources and are
   recorded in `07-ifrs18-source-verification.md`, but the environment could not
   retrieve the primary pages, so every rule is marked `VERIFIED-SECONDARY`.
   Five items remain open there — two of them affect rules that fire on ordinary
   non-financial corporates.
4. **Provide one real anonymised Korean income statement** as the fixture. Every
   phase's exit criterion above is defined against a real statement; a synthetic
   one would validate the code but not the dictionary or the rule set.
