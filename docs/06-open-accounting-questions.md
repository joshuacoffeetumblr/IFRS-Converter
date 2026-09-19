# Open Accounting Questions — decisions required before Phase 5

Per spec §35: *"회계 기준에 대한 불확실성이 있는 부분은 임의로 결정하지 말고 명시적으로 표시한다."*
Nothing below has been silently decided. Each item blocks or constrains a
specific implementation task.

---

## Decision status

| Q | Topic | Status | Decision |
|---|---|---|---|
| Q1 | Sixth category (`OTHER_RELEVANT_CATEGORY`) | ✅ **RESOLVED** 2026-09-19 | Redefined as `UNCLASSIFIED`, a technical non-IFRS state |
| Q2 | Definition of "before" operating profit | ✅ **RESOLVED** 2026-09-19 | **As reported**; any unattributed difference shown explicitly in the waterfall |
| Q3 | Is `PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES` always presented? | 🔬 researched | **IFRS 18.73** — a *prohibition*, narrowly scoped; handling decision open |
| Q4 | Derivatives and hedging | 🔬 researched | **IFRS 18 B72** — rule exists; `NEEDS_FACT` vs blanket review open |
| Q5 | Korean statement conventions (금융수익/비용 등) | 🔬 partly researched | 지분법손익 resolved (**always investing**); aggregate-caption handling open |
| Q6 | Spec §30's "30 → 20" expectation | ⛔ **OPEN** — needs your intent | — |
| Q7 | Scope: P&L only? | ✅ **RESOLVED** 2026-09-19 | P&L only, implied by MVP scope approval ("손익계산서만"). MPM and OCI restructuring are stated limitations. |

Phases 1–4 are unblocked. Q3–Q6 must be settled before `classification_rules`
seed data is written in Phase 5. Paragraph-level citations are recorded in
`07-ifrs18-source-verification.md`.

---

## Q1 — `OTHER_RELEVANT_CATEGORY` (spec §9, §2, §19) — ✅ RESOLVED

Spec §9 lists six enum values and §2/§19 refer to "other relevant categories".
IFRS 18 defines **five** categories for profit or loss: operating, investing,
financing, income taxes, discontinued operations. Operating is the residual, so
there is no sixth bucket.

**Proposal:** keep the enum slot, redefine it as `UNCLASSIFIED` — a technical
state meaning "the engine could not decide and no human has yet". It never
appears in a finalized statement and its presence blocks finalization.

**Alternative:** you intended "other relevant categories" to mean the *other four
non-operating categories collectively*, in which case no enum value is needed at
all and §2's example section is simply "Income tax / Discontinued operations".

**DECISION (2026-09-19): `UNCLASSIFIED` confirmed.** The enum slot is retained
with the technical meaning above. It is not an IFRS 18 category, never appears
in a finalized statement, and blocks finalization while present. Implemented in
`Ifrs18Category` (`04-classification-engine.md` §3).

---

## Q2 — What is the "before" operating profit? — ✅ RESOLVED

The headline number in spec §5 and §22 is *"Operating Profit — Current:
₩1,240bn"*. There are two defensible sources and they can differ materially:

- **(a) As reported.** The 영업이익 subtotal printed in the entity's own
  statement, prepared under K-IFRS / IAS 1. This is what a reader recognises,
  but IAS 1 did not define operating profit, so it is not comparable between
  entities — that is one of the problems IFRS 18 was issued to address.
- **(b) Reconstructed.** Sum of the lines the entity presented above its
  operating subtotal, recomputed by us.

They diverge whenever the entity's own subtotal includes items we could not
attribute to lines (e.g. an aggregated 기타영업외손익), or when the entity's
operating subtotal is non-standard.

**Proposal:** use **(a) as reported** as the headline "Before", because the user's
question is "how does my published operating profit change?". Compute (b) as a
cross-check and, when (a) ≠ (b), display the difference as an explicit
"unattributed" step in the waterfall rather than absorbing it.

**DECISION (2026-09-19): option (a), as reported.** The headline "Before" is the
entity's own published 영업이익 subtotal. The reconstructed figure (b) is still
computed as a cross-check, and where (a) ≠ (b) the difference is rendered as an
explicit `UNATTRIBUTED` step in the waterfall rather than absorbed. Consequence
for the schema: `financial_statement_lines.subtotal_kind` must capture
`REPORTED_OPERATING_PROFIT`, and extraction fails loudly if a statement has no
identifiable reported operating subtotal.

---

## Q3 — Is `PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES` always presented? — 🔬 RESEARCHED

IFRS 18 requires the subtotal "profit or loss before financing and income taxes",
but I understand there is an **exception for entities that classify financing- or
cash-related income and expenses in the operating category** because of a
specified main business activity (typically banks and similar financiers)
⚠ VERIFY against the issued standard.

**Impact:** the statement renderer and the KPI list must know whether to present
it. Getting this wrong produces a statement that is wrong on its face for exactly
the entities where IFRS 18 matters most.

### Research finding (2026-09-19) — the hypothesis above was wrong in two ways

See `07-ifrs18-source-verification.md` F9. The governing paragraph is
**IFRS 18.73**, and:

1. **It is a prohibition, not an exemption.** The affected entity is *not
   permitted* to present the subtotal. It is not a matter of "not required".
2. **The condition is much narrower** than "financial institutions". It applies
   to an entity whose specified main business activity is providing financing to
   customers **and** which classifies in the operating category the interest
   expense on liabilities *unrelated* to providing financing to customers.

Also established: operating profit and profit before financing and income taxes
are **both presented even when they are equal** — an entity with no investing
items still presents both. And an entity caught by paragraph 73 may add its own
subtotal under **paragraph 24**, but must **not** label it in a way implying
financing amounts are excluded ("profit before financing" is misleading and not
permitted).

**Remaining decision:** the approved MVP scope excludes banks, insurers and
securities firms, so the paragraph 73 path is never exercised on a valid MVP
input. What should happen if a user nonetheless confirms
`PROVIDING_FINANCING_TO_CUSTOMERS = true`?

---

## Q4 — Derivatives and hedging — 🔬 RESEARCHED

Classification of gains and losses on derivatives depends on whether the
derivative is used for risk management, whether hedge accounting is designated,
and what risk is hedged. This cannot be determined from an account name and
amount alone; it requires the hedging note.

### Research finding (2026-09-19) — there *is* a rule, and it is usable

See `07-ifrs18-source-verification.md` F6 and F7. **IFRS 18 paragraph B72**:
gains and losses on a derivative, and on an instrument designated as a hedging
instrument, are classified **in the same category as the income and expenses
affected by the risks the instrument is used to manage**. This covers both
designated hedging instruments and non-designated derivatives used to manage an
identified risk. Where that would require **grossing up** gains and losses, or
involve **undue cost or effort**, **all** gains and losses on the derivative go
to **operating**.

An April 2026 IFRIC agenda decision (F7) applies B72 to a group hedging a net
foreign-currency exposure and concludes the derivative follows the category of
the **net exposure being managed** — financing, in that fact pattern.

This changes the proposal. The blocker is not that no rule exists; it is that
**the rule's input — which risk the derivative manages — cannot be derived from
an account name and an amount.** It is a fact about the entity, exactly like a
specified main business activity.

**Revised proposal:** treat it as a `NEEDS_FACT` rule rather than a blanket
"always review". The engine detects a derivative line, asks a structured
question ("what risk does this instrument manage?"), and then applies B72
deterministically to the answer, with an explicit "grossing up / undue cost or
effort" option that routes to operating. The decision becomes auditable and
cites B72, instead of being an unexplained human choice.

**Remaining decision:** adopt the B72 `NEEDS_FACT` rule, or keep the simpler
blanket human review for MVP?

---

## Q5 — Korean statement conventions — 🔬 PARTIALLY RESEARCHED, decision open

Points requiring domain confirmation before the normalization dictionary is
seeded:

1. **금융수익 / 금융비용.** These K-IFRS captions aggregate items that IFRS 18
   splits across investing and financing (interest on deposits → investing;
   interest on borrowings → financing). When a statement presents only the
   aggregate, we cannot split it from the face of the statement.

   **Important refinement.** In a Korean statement these captions sit *below*
   영업이익, so splitting them between investing and financing does **not** move
   operating profit — it only changes PBFIT and the investing/financing KPIs.
   **But it is not harmless**, because **IFRS 18 B65** (F5) sends a foreign
   exchange difference to the category of the item that produced it, and FX on a
   **trade receivable goes to operating**. A 금융수익 bucket containing 외환차익
   on trade receivables therefore *does* change operating profit once
   decomposed. Leaving it aggregated silently understates the operating effect.
2. **지분법손익 (equity-method share of profit).** ✅ **Resolved, and my earlier
   draft was wrong.** Equity-method results from associates, joint ventures and
   unconsolidated subsidiaries are classified in **investing unconditionally** —
   regardless of the entity's business model, and even if they arise from a main
   business activity. There is **no** specified-main-business-activity exception,
   so `IFRS18-SMBA-001` must **not** touch them. See
   `07-ifrs18-source-verification.md` F3.
3. **영업외수익 / 영업외비용.** Legacy non-operating captions, frequently
   aggregated. Under IFRS 18 many of their contents are **operating**, because
   operating is the residual category (F2). **This is very likely the single
   largest driver of operating-profit change for Korean non-financial
   corporates**, and it only materialises if the caption is decomposed.
   **Proposal:** never map these to a category directly; always decompose to
   detail lines, and if no detail exists, require review.
4. **Scale and units.** Korean statements are commonly presented in 백만원 or
   천원 and sometimes mix units between the statement and the notes. Captured as
   `presentation_scale` per statement, with a validation warning when a
   statement's magnitudes are inconsistent with its declared scale.

---

## Q6 — Spec §30's "30 → 20" test expectation

Spec §30 gives Revenue 100, Operating expense −70, Interest income 10, Finance
cost −5, expected operating profit 30 (✓ confirmed in test vector T1), then says
operating profit should move "30 → 20" on a classification change. From these
four figures the reclassification of interest income yields **40 → 30**, not
30 → 20; no reclassification among these lines produces 20.

**Needed from you:** the intended scenario, so T2 can assert the number you
actually want. The invariant that ΔPBT = 0 holds in every reading and is already
asserted.

---

## Q7 — Scope of restructuring: P&L only? — ✅ RESOLVED

IFRS 18 also changes OCI presentation, requires disclosure of
management-defined performance measures (MPMs), and sets aggregation /
disaggregation requirements. Spec §2–§7 and §34 are entirely about the statement
of profit or loss.

**Proposal:** MVP restructures **profit or loss only**. Balance sheet data is
ingested solely to support extraction reconciliation (§17). MPMs, OCI
restructuring and aggregation requirements are explicitly deferred and named as
out of scope in the product's own limitations text, so users are not misled into
thinking the output is a complete IFRS 18 compliance assessment.

**DECISION (2026-09-19): P&L only**, carried by the MVP scope approval
("손익계산서만"). MPM disclosure, OCI restructuring and the aggregation /
disaggregation requirements are out of MVP scope and must appear as **stated
limitations in the product's own text**, alongside the §24 disclaimer — not as
silent omissions.

---

## Q5 decision required — aggregated caption handling

Items 1 and 3 above converge on one product decision: what does the MVP do when
the face of the statement shows only an aggregate (금융수익, 영업외수익,
영업외비용) with no detail lines?

This is not a cosmetic choice. Under IFRS 18 the residual rule (F2) and the FX
rule (F5) mean an undecomposed 영업외수익 bucket can hide a real operating-profit
change — which is the number the entire product exists to explain.

Options are put to the user in the accompanying discussion.

## Q6 — still open

Requires the user's intent; no external source can resolve it. See the Q6
section above.
