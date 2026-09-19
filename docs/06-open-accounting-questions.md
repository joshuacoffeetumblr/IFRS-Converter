# Open Accounting Questions — decisions required before Phase 5

Per spec §35: *"회계 기준에 대한 불확실성이 있는 부분은 임의로 결정하지 말고 명시적으로 표시한다."*
Nothing below has been silently decided. Each item blocks or constrains a
specific implementation task.

---

## Q1 — `OTHER_RELEVANT_CATEGORY` (spec §9, §2, §19) — **blocks ERD freeze**

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

**Needed from you:** confirm `UNCLASSIFIED`, or tell me the intended meaning.

---

## Q2 — What is the "before" operating profit? — **blocks the entire impact screen**

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

**Needed from you:** confirm (a), and confirm that showing the unattributed
difference openly is acceptable even though it makes some waterfalls less tidy.

---

## Q3 — Is `PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES` always presented?

IFRS 18 requires the subtotal "profit or loss before financing and income taxes",
but I understand there is an **exception for entities that classify financing- or
cash-related income and expenses in the operating category** because of a
specified main business activity (typically banks and similar financiers)
⚠ VERIFY against the issued standard.

**Impact:** the statement renderer and the KPI list must know whether to present
it. Getting this wrong produces a statement that is wrong on its face for exactly
the entities where IFRS 18 matters most.

**Proposal:** model it as a computed flag `subtotal.presented`, derived from the
confirmed main business activities, and default to `true`. **Verify the exact
condition against the standard text before seeding `IFRS18-SMBA-002`.**

**Needed from you:** confirm that verification against the IFRS Foundation text
is in scope for Phase 5 (it requires access to the standard), and tell me whether
banks / financial institutions are in or out of the MVP — see MVP §3.

---

## Q4 — Derivatives and hedging (`IFRS18-LINKED-002`) — **highest uncertainty**

Classification of gains and losses on derivatives depends on whether the
derivative is used for risk management, whether hedge accounting is designated,
and what risk is hedged. This cannot be determined from an account name and
amount alone; it requires the hedging note.

**Proposal for MVP:** do **not** attempt an automatic rule. Route every
derivative-related line to human review with a targeted question, an explanatory
note, and a link to the relevant disclosure. Partial automation here would create
confident-looking wrong answers, which is the worst outcome for this product.

**Needed from you:** accept "always human review" for derivatives in MVP.

---

## Q5 — Korean statement conventions

Points requiring domain confirmation before the normalization dictionary is
seeded:

1. **금융수익 / 금융비용.** These K-IFRS captions aggregate items that IFRS 18
   splits across investing and financing (interest on deposits → investing;
   interest on borrowings → financing). When a statement presents only the
   aggregate, we cannot split it from the face of the statement.
   **Proposal:** flag the aggregate as `requires_human_review`, show the amount,
   and ask the user to either split it or accept a single classification with the
   limitation recorded in the audit trail. **Confirm.**
2. **지분법손익 (equity-method share of profit).** Classified as investing under
   `IFRS18-INVESTING-001` ⚠ VERIFY — but this is also a case where an entity with
   an investing main business activity would classify it operating. Handled by
   `IFRS18-SMBA-001`.
3. **영업외수익 / 영업외비용.** Legacy non-operating captions, frequently
   aggregated. Under IFRS 18 many of their contents are operating (residual).
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

## Q7 — Scope of restructuring: P&L only?

IFRS 18 also changes OCI presentation, requires disclosure of
management-defined performance measures (MPMs), and sets aggregation /
disaggregation requirements. Spec §2–§7 and §34 are entirely about the statement
of profit or loss.

**Proposal:** MVP restructures **profit or loss only**. Balance sheet data is
ingested solely to support extraction reconciliation (§17). MPMs, OCI
restructuring and aggregation requirements are explicitly deferred and named as
out of scope in the product's own limitations text, so users are not misled into
thinking the output is a complete IFRS 18 compliance assessment.

**Needed from you:** confirm, and in particular confirm that **MPM disclosure is
out of MVP scope** — it is a prominent IFRS 18 requirement and its absence should
be a stated limitation rather than an omission.
