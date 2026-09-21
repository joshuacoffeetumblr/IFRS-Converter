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
| 18 | Read a DART XBRL instance directly, including the note breakdowns | §3 |

### Out of scope for MVP (and stated as limitations in-product)

| Excluded | Why | Spec ref |
|---|---|---|
| Scanned/OCR PDFs | Separate problem; high silent-error risk | §33 |
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
| **3a** | XLSX ingest with provenance + extraction reconciliation | ✅ done 2026-09-19 against synthetic fixtures; a **real** statement is still outstanding — see §7 |
| **3b** | CSV ingest | ✅ done 2026-09-19 — shares one pipeline with XLSX; Korean encodings (CP949/EUC-KR/BOM) handled |
| **3c** | PDF ingest (text-based only) | ✅ done 2026-09-20 — pdfplumber word coordinates grouped into rows by baseline and into columns by **page-wide bands**; per-row column indexing silently dropped every subtotal, because a subtotal carries no note reference and its figure landed in the note column. A PDF with no text layer is refused with a message saying so, never OCR'd. |
| **4** | Normalization dictionary (Korean + English), synonyms, fuzzy matching, **note-based decomposition of aggregate captions (Q5)** | ✅ done 2026-09-19 — 100% without AI on both the canonical and the messy fixture; decomposition refuses components that do not sum to the caption |
| **5** | **Rule engine, rule seed data with verified citations, company- and line-scoped `NEEDS_FACT` (Q4), AI advisor** | ✅ done 2026-09-20 — 10 cited rules, all `VERIFIED_SECONDARY`; the two with unconfirmed conditions force human review. AI advisor added 2026-09-20: schema-constrained output built from the domain enums, one repair round trip, discarded if it still does not validate — and off unless a key and a switch are both set. |
| **6** | Reconstruction, subtotals, reconciliation gate | ✅ done 2026-09-19 — total invariance exact on every fixture; the gate blocks on an unclassified line, an unanswered question, or a subtotal IFRS 18.73 forbids |
| **7** | Impact: KPIs, waterfall, drivers | ✅ done 2026-09-19 — the waterfall is built from the movement the gate checks, so it sums to the delta by construction; measures IFRS 18 introduced report no "before" rather than a false zero |
| **8** | Excel export (+ PDF if time) | ✅ done 2026-09-19 — six sheets incl. audit trail and reconciliation; a figure that would lose precision fails the export rather than shipping altered; unreconciled files are watermarked on every sheet. **PDF export not built** — Excel satisfies §34. |
| **9** | Frontend screens: projects, upload/extract, review, statement, impact, audit trail | ✅ done 2026-09-20 — Playwright drives the whole §34 flow end to end against a live API; the session token lives in an httpOnly cookie and never reaches JavaScript; every figure crosses as a string and is formatted, never computed, in the browser |

**Phase 0 closed 2026-09-19.** Q1, Q2 and Q7 are resolved, so schema and
impact-screen work is unblocked. Q3–Q6 remain open and block Phase 5 rule
seeding only; Phases 1–4 proceed.

### 4.1 Deployment

| Item | State |
|---|---|
| Production image | ✅ two stages; the dev extra (pytest, ruff, mypy, reportlab) is not installed, and the compiler does not follow into the runtime |
| Production compose overlay | ✅ `docker-compose.prod.yml` — every secret required with no default, no published database port, no source mounts, no `--reload` |
| Refuse to start on a dangerous configuration | ✅ unset or short signing key, debug mode, the repository's own database credentials, and a wildcard / plaintext / localhost CORS origin — all reported at once |
| CI: production install | ✅ builds the wheel, installs it with **no** dev extra, imports the app and runs the harness. This is how `email-validator` was found missing: `EmailStr` needs it at import time, the dev extra supplied it, and the production image could not import `app.main` at all |
| CI: end-to-end | ✅ Playwright against a live API and the standalone Next build — not `next start`, which serves a different bundle from the one that was built |

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

## 6. Validating against a real statement

Every number in this repository comes from a fixture we wrote, which means the
one thing none of it proves is that the reader handles a document somebody else
produced. A synthetic statement cannot fail extraction reconciliation in an
interesting way: we built it to add up.

That gap cannot be closed by writing more tests. It closes when an anonymised
Korean filing is supplied. So the harness that consumes one exists now:

    make validate f=손익계산서.xlsx
    python -m app.cli validate 손익계산서.pdf --out report.md

No database, no network, so it runs on a file that may not be uploaded
anywhere. It reports, in pipeline order: whether the document's own subtotals
reproduce (§17 — the check that actually matters), dictionary coverage **and
the captions it did not recognise**, which rules fired, the facts a reviewer
would be asked for, and whether the §19 gate opens. It exits non-zero when the
file would not produce a shippable result.

The expected first outcome on a real filing is a list of unrecognised captions,
not a pass. That list is the work item.

### 6.0 Validated against a real filing — 2026-09-20

Samsung Electronics' 2026 half-year DART filing (entity `00126380`), as XBRL:
instance, schema, presentation, calculation and English label linkbases. The
consolidated income statement (role `D310000`, 기능별) was taken from the
presentation linkbase, its figures from the `CFY2026dHYA` consolidated context,
and its captions from the filing's own English label linkbase.

**Result: extraction reconciles exactly.** All five §17 checks agree to the
won, on figures the filing prints entirely unsigned — the signs were derived
and then proved against its own subtotals (§16). Dictionary coverage 100% on
all nine detail lines, total invariance holds.

**The gate correctly stays shut.** `Other gains`, `Other losses`,
`Finance income` and `Finance costs` remain UNCLASSIFIED pending note-based
decomposition — which is the right answer, because those four aggregates are
exactly what IFRS 18 exists to look inside. The filing's own notes (roles
`D834320`, `D834330`) contain the breakdown.

Three defects were found and fixed getting there, all the same root cause:
**every caption table in the ingest layer was Korean-only**, while the product
claims Korean + English. They fail in a chain.

| Table | Consequence |
|---|---|
| `classify_subtotal` | No English subtotal recognised → the statement had **no subtotals** → its own arithmetic could never be checked → refused as unreadable (§17), however perfectly extracted |
| account dictionary | The four unrecognised subtotals then went through account matching as ordinary lines, so coverage read **46%** when the real figure was 100% |
| `_looks_like_expense` | No English deduction recognised → a filing printing every figure unsigned, which is the Korean convention and unaffected by English captions, could never have its signs derived |

Two things the fix had to get right. English subtotals are matched
**exactly**, not by prefix: `Profit (loss)` reduces to `profit`, and prefix
matching would make a subtotal of `Profit from disposal of investments` —
silently adding a detail line to the total it was there to verify. And a
parenthetical qualifier is stripped before testing for a deduction, because
IFRS labels use it to make a caption sign-neutral: `Share of profit (loss) of
associates` is income, and matching `loss` inside it would have turned a
₩484bn gain into a deduction of the same size.

The English synonyms added are the IFRS taxonomy's own standard labels, taken
from the filing's label linkbase rather than guessed.

### 6.0.1 Taken all the way through, 2026-09-20

The filing's own notes (roles `D834320`, `D834330`) decompose all four blocked
aggregates, and every one of them sums back to its caption exactly — which is
the §4/Q5 decomposition requirement, met by a real document rather than a
fixture. Fed the decomposed statement, the pipeline runs end to end:

| Stage | Result |
|---|---|
| Extraction | 5/5 §17 checks exact, signs derived and proved |
| Normalization | 18/18 detail lines, 0 needing decomposition |
| Classification | 8 of the 10 rules fire — B65 (FX, 2 lines), B72 (derivatives, 2), ¶49-50 (investing, 4), financing (1), tax (1) |
| Questions | 4 raised: 2 company-scoped, 2 line-scoped — and the gate stays shut until they are answered |
| Gate, once answered | PASSED, 0 UNCLASSIFIED, total invariance exact |

**The most important line of that table is the fourth.** With eight lines
blocked on unanswered facts the product refuses to produce a number, and it
does not infer from "this is a manufacturer" whether investing in assets is a
main business activity (§10). Answering the four questions is an accounting
determination, and it belongs to a person.

A third round of the same lexical defect was found here, and it is the most
expensive one yet. The dictionary already held every account the rules key on —
`DERIVATIVE_GAIN`, `DIVIDEND_INCOME`, `RENTAL_INCOME` and the rest — but its
English synonyms did not carry the IFRS taxonomy's spellings: `Gain from
derivatives` missed `Gain on derivatives`, `Lent income` missed `Rental
income`, `Interest expense, finance expense` missed `Interest expense`. Those
lines fell to the operating residual **silently**. Nothing failed; the
reclassification simply did not happen, and the reported IFRS 18 impact would
have been understated by the whole of the B72 and ¶49-50 effect.

The tests added for it assert that the **rule fires**, not that the caption
matched — a synonym resolving to an account no rule looks at is
indistinguishable in the output from no synonym at all.

### 6.0.2 Accounting decisions taken here, for review

Three accounts were added, and two synonyms moved, on judgement rather than
on a defect. They are listed for an accountant to confirm or reverse:

| Change | Reasoning |
|---|---|
| New `MISCELLANEOUS_INCOME` (잡이익) and `MISCELLANEOUS_LOSS` (잡손실) as **leaves** | They are already the residual *inside* a note, so there is nothing further to decompose, and operating follows from the standard's residual rule. Mapped to 기타수익/기타비용 they inherited `ambiguous_by_default` and stayed permanently UNCLASSIFIED — a fully decomposed real filing could never finalize, whatever the reviewer did. |
| New `DONATIONS` (기부금) | Operating by the residual rule: a donation is neither a return on an investment nor a cost of obtaining finance, so IFRS 18 gives it no positive reason to leave operating. |
| 잡이익/잡손실 removed from 기타수익/기타비용's synonyms | Otherwise the new leaves are unreachable. 기타수익 and 기타비용 themselves stay aggregates — they are exactly what IFRS 18 wants looked inside — and a test asserts it. |

### 6.1 What asking the question already found

Before a real file arrived, asking "what about 삼성전자's statement?" was enough
to find a defect, because a filing's *shape* can be reproduced even when its
figures cannot. A DART 재무제표 download differs from our fixtures in four
ways, and one of them broke the reader:

**The note column holds bare numbers.** Our fixtures print `주석 21`, which
cannot be read as a figure. Filings print `21`. Sitting left of the amounts, it
was selected as the current period — so every amount became a note number, and
every row without a note, which is **every subtotal**, disappeared for having no
amount. The same root cause meant note references were never collected at all,
so note-based decomposition of an aggregate caption (B65, B72) would have had
nothing to work from.

The §17 reconciliation caught it — the verdict was MISREAD and no figure would
ever have reached a screen. But "unreadable" is the wrong answer to a file we
should read, and our own fixtures could not produce it.

Fixed by surveying candidate columns across the whole grid instead of judging
from one row: a note column is **sparse** (blank on every subtotal) and holds
only small positive integers, and a `주석` header settles it outright. Magnitude
alone decides nothing — a statement presented in 십억원 has real figures in the
same range. All three adapters share the grid, so all three were affected and
all three are fixed; a test asserts they still produce identical figures.

The other three differences turned out to be handled: Roman-numeral caption
prefixes (`Ⅲ. 매출총이익`) normalize, three comparative periods are reachable,
and won-scale figures — 15-digit integers, with no presentation unit — survive
exactly. That last one has a ceiling, and it is the spreadsheet's, not ours:
a double holds integers exactly to 2⁵³, about 9,000조, which is two orders of
magnitude above the largest line in any Korean income statement.

## 7. Outstanding inputs needed

1. ~~Approve or amend this scope~~ — ✅ approved 2026-09-19.
2. ~~Answer Q3–Q6~~ — ✅ all resolved 2026-09-19.
3. **An anonymised Korean income statement.** Still outstanding, and the only item here that cannot be resolved from inside the repository — see §6.
3. **Confirm access to the issued text of IFRS 18** (spec §25). Citations were
   verified on 2026-09-19 against IFRS Foundation and Big 4 sources and are
   recorded in `07-ifrs18-source-verification.md`, but the environment could not
   retrieve the primary pages, so every rule is marked `VERIFIED-SECONDARY`.
   Five items remain open there — two of them affect rules that fire on ordinary
   non-financial corporates.
4. **Provide one real anonymised Korean income statement** as the fixture. Every
   phase's exit criterion above is defined against a real statement; a synthetic
   one would validate the code but not the dictionary or the rule set.

### 6.1 XBRL was moved into scope — 2026-09-21

§3 listed XBRL as future work and §33 excluded it. Validating against the
Samsung filing (6.0) inverted the argument: XBRL turned out to be the *easiest*
format to read correctly and the only one that answers the question the product
exists to ask.

Every other reader spends its effort guessing — which column holds the figures,
which row is a subtotal, whether an unsigned number is a deduction, what unit
the page is in. Each of those guesses has been a defect at least once, and 6.0's
three defects were all the same guess made in three places. An XBRL instance
*states* all of it:

| What the other readers infer | What the filing declares |
|---|---|
| which caption is a subtotal | the concept, via the taxonomy |
| whether a figure is a deduction | the concept's nature |
| which period a column is | the context's period |
| consolidated or separate | the context's dimension |
| what the caption means | the concept id, in any language |

And it closes 6.0's open item without a person doing anything. The four
aggregates that correctly stayed UNCLASSIFIED — 기타수익, 기타비용, 금융수익,
금융비용 — are broken down in the filing's own notes, tagged under the same
period and basis. The reader substitutes the components for the caption **only
where they add up to it exactly**; a breakdown that does not reconcile is not
one we have understood, and an approximate split of 금융수익 would be worse than
none, because ¶49-50, B65 and B72 each classify a different part of it.

On the Samsung filing all four reconcile to the won, and the statement goes from
9 detail lines to 18. What was one UNCLASSIFIED aggregate is now 이자수익,
외환차익 and 파생상품이익 — three lines, three different rules, each asking the
reviewer a question that has an answer.

The narrowness is deliberate: a concept this product does not know is reported
rather than silently dropped, because a filing we only partly read is exactly
what the §17 reconciliation exists to catch.
