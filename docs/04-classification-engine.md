# Task 5 — IFRS 18 Classification Engine: Domain Model

> Status: **proposal, awaiting approval.**
>
> **Citation caveat (spec §25).** Paragraph references below are marked
> `⚠ VERIFY`. They record *which requirement* each rule implements and must be
> confirmed against the IFRS Foundation's issued text of IFRS 18 before any rule
> is seeded into `classification_rules`. The requirement descriptions reflect
> IFRS 18 *Presentation and Disclosure in Financial Statements* (issued April
> 2024, effective for annual reporting periods beginning on or after 1 January
> 2027, early application permitted). Verifying the exact paragraph numbers and
> wording is an explicit Phase 5 task, not an assumption to build on.

## 1. The five categories, and why operating is special

IFRS 18 classifies income and expenses in the statement of profit or loss into
five categories:

| Category | Nature |
|---|---|
| `OPERATING` | **Residual.** Everything not classified elsewhere. |
| `INVESTING` | Returns from assets generating returns largely independently ⚠ VERIFY |
| `FINANCING` | Income/expenses from raising finance, plus interest on other liabilities ⚠ VERIFY |
| `INCOME_TAX` | Income tax expense/income |
| `DISCONTINUED_OPERATION` | Results of discontinued operations |

**The engine is built around the residual property.** Rules positively identify
investing, financing, tax and discontinued items. Anything not matched is
operating *by definition of the standard*, not by inference. This is why a
deterministic engine can cover most of a statement and the AI layer stays small.

### On `OTHER_RELEVANT_CATEGORY` (spec §9) — flagged for decision

Spec §9 lists `OTHER_RELEVANT_CATEGORY` as a sixth enum value, and spec §2's
example shows an "Other / relevant categories" section. **IFRS 18 defines five
categories for profit or loss; there is no sixth residual "other" category** —
that role is filled by `OPERATING`.

Proposal: retain the enum slot but rename its meaning to a **technical, non-IFRS
state**:

```
UNCLASSIFIED   — the engine could not decide and no human has yet.
                 NEVER appears in a finalized statement.
                 Its presence blocks finalization.
```

This preserves the extensibility §9 asks for without inventing an accounting
category that does not exist. **Decision required from you** — see
`06-open-accounting-questions.md` Q1.

## 2. Subcategories

Top-level category drives all arithmetic; subcategory is presentational detail
(ERD §4.2).

```
OPERATING       → OPERATING_REVENUE | OPERATING_EXPENSE | OPERATING_OTHER
INVESTING       → INVESTING_INCOME  | INVESTING_EXPENSE
FINANCING       → FINANCING_INCOME  | FINANCING_EXPENSE
INCOME_TAX      → INCOME_TAX_EXPENSE | INCOME_TAX_INCOME
DISCONTINUED_OPERATION → DISCONTINUED_RESULT
UNCLASSIFIED    → (none)
```

Subcategory may be `None`. No subtotal ever depends on it.

## 3. Core domain types (pure Python, no I/O)

```python
# domain/ifrs18/types.py
class Ifrs18Category(StrEnum):
    OPERATING = "OPERATING"
    INVESTING = "INVESTING"
    FINANCING = "FINANCING"
    INCOME_TAX = "INCOME_TAX"
    DISCONTINUED_OPERATION = "DISCONTINUED_OPERATION"
    UNCLASSIFIED = "UNCLASSIFIED"

class ClassificationMethod(StrEnum):
    RULE = "RULE"                        # deterministic rule matched
    RESIDUAL_DEFAULT = "RESIDUAL_DEFAULT"# no rule matched → operating by definition
    AI = "AI"                            # AI proposal, never final on its own
    USER = "USER"                        # human decision, always final
    UNRESOLVED = "UNRESOLVED"            # blocked on an unanswered fact

class ActivityType(StrEnum):
    INVESTING_IN_ASSETS = "INVESTING_IN_ASSETS"
    PROVIDING_FINANCING_TO_CUSTOMERS = "PROVIDING_FINANCING_TO_CUSTOMERS"
    FINANCIAL_SERVICES = "FINANCIAL_SERVICES"
    INVESTMENT = "INVESTMENT"
    LENDING = "LENDING"
    REAL_ESTATE = "REAL_ESTATE"
    OTHER = "OTHER"

@dataclass(frozen=True)
class EntityFacts:
    """Confirmed facts about the entity. Tri-state by design."""
    main_business_activities: Mapping[ActivityType, bool | None]

    def is_main(self, a: ActivityType) -> bool | None:
        return self.main_business_activities.get(a)   # None == UNKNOWN

@dataclass(frozen=True)
class ClassifiableItem:
    line_id: UUID
    raw_label: str
    normalized_account_code: str | None
    amount: Decimal                 # signed P&L effect
    current_category: str | None
    statement_section: str
    note_references: tuple[str, ...]
    is_subtotal: bool               # must be False to reach the engine

@dataclass(frozen=True)
class RuleOutcome:
    status: Literal["MATCH", "NO_MATCH", "NEEDS_FACT"]
    category: Ifrs18Category | None = None
    subcategory: str | None = None
    required_fact: ActivityType | None = None
    rule_id: str | None = None

@dataclass(frozen=True)
class ClassificationDecision:
    line_id: UUID
    category: Ifrs18Category
    subcategory: str | None
    method: ClassificationMethod
    rule_id: str | None
    confidence: Decimal | None
    reasoning: str | None
    evidence: tuple[Evidence, ...]
    requires_human_review: bool
    blocked_on_question_key: str | None
```

`EntityFacts.is_main()` returning `None` is the mechanism that produces user
questions. `None` (unknown) and `False` (user said no) are never conflated.

## 4. The `NEEDS_FACT` outcome

This is the engine's most important structural feature and the reason the
question in spec §5 is deterministic rather than AI-generated.

```
rule matches the account pattern
        │
        ├─ required fact is known ────────────► MATCH (category depends on the fact)
        └─ required fact is None (unknown) ───► NEEDS_FACT
                                                  │
                                                  ▼
                                    review_questions row created
                                    classification.method = UNRESOLVED
                                    finalization blocked
                                                  │
                                    user answers YES / NO
                                                  ▼
                                    rule re-evaluated → MATCH
                                    (NOT_SURE → stays UNRESOLVED, still blocked)
```

## 5. Evaluation pipeline

```
for each line:
  0. guard        is_subtotal → skip entirely (never classified, never summed)
  1. tax          → INCOME_TAX
  2. discontinued → DISCONTINUED_OPERATION
  3. SMBA-dependent rules       → MATCH | NEEDS_FACT
  4. investing rules            → MATCH | NO_MATCH
  5. financing rules            → MATCH | NO_MATCH
  6. linked-item rules (FX, derivatives, impairment) → inherit category of the
                                                        underlying item ⚠ VERIFY
  7. ambiguity detection        → send to AI advisor (Layer 2)
  8. residual                   → OPERATING, method = RESIDUAL_DEFAULT
```

Rules are ordered by `priority`, **first match wins**, and the winning `rule_id`
is recorded. Two rules matching the same line with different categories is a
seed-data defect, detected by a startup consistency check over the rule set.

### Step 7: when is an item "ambiguous"?

An item goes to the AI advisor only if **all** of these hold — this keeps AI
usage small, cheap and auditable:

- no rule matched, **and**
- the label did not normalize to a known account (`normalization_score` below
  threshold or `normalized_account_id IS NULL`), **or** the normalized account is
  flagged `ambiguous_by_default` (aggregate buckets like `기타수익`, `기타영업외비용`), **and**
- the amount is material relative to the configured materiality floor.

Immaterial unmatched items go straight to `RESIDUAL_DEFAULT` with
`requires_human_review = false`, and are listed in a "not individually reviewed"
appendix in the export. Materiality is config, not a magic number (spec §29).

## 6. Rule condition DSL

Rules are data (ERD `classification_rules.condition`, `jsonb`), evaluated by a
small total interpreter — no `eval`, no code loading from the database.

```json
{ "all": [
    { "field": "normalized_account_code", "op": "in",
      "value": ["INTEREST_INCOME_ON_CASH", "INTEREST_INCOME_ON_DEPOSITS"] },
    { "field": "entity.main_business_activity.INVESTING_IN_ASSETS",
      "op": "is", "value": false }
] }
```

Supported: `all`, `any`, `not`; operators `eq`, `in`, `matches` (anchored RE2-safe
regex), `is` (tri-state: `true` / `false` / `null`), `gte`, `lt`.
Fields are a fixed whitelist. An unknown field is a rule-set validation error at
load time, never a silent `NO_MATCH`.

Why data and not Python functions: spec §25 requires each rule to carry its
authoritative source and effective date, and §8 requires `rule_id` in the audit
trail. Data-driven rules make the rule set exportable, diffable, reviewable by an
accountant who does not read Python, and versionable independently of releases.

## 7. Initial rule set (draft — every citation requires verification)

| rule_id | Requirement implemented | Category | Requires fact | Source ⚠ VERIFY |
|---|---|---|---|---|
| `IFRS18-TAX-001` | Income tax expense/income | `INCOME_TAX` | — | IFRS 18 income-tax category |
| `IFRS18-DISC-001` | Results of discontinued operations | `DISCONTINUED_OPERATION` | — | IFRS 18 discontinued category; IFRS 5 |
| `IFRS18-INVESTING-001` | Income/expenses from investments in associates, JVs and unconsolidated subsidiaries (incl. equity-method share of profit) | `INVESTING` | — | IFRS 18 investing category |
| `IFRS18-INVESTING-002` | Income/expenses from cash and cash equivalents (interest on deposits) | `INVESTING` | `INVESTING_IN_ASSETS` | IFRS 18 investing category |
| `IFRS18-INVESTING-003` | Income/expenses from other assets generating a return individually and largely independently of other resources | `INVESTING` | `INVESTING_IN_ASSETS` | IFRS 18 investing category |
| `IFRS18-FINANCING-001` | Income/expenses from liabilities arising from transactions involving **only** the raising of finance (borrowings, bonds) | `FINANCING` | `PROVIDING_FINANCING_TO_CUSTOMERS` | IFRS 18 financing category |
| `IFRS18-FINANCING-002` | Interest expense and effects of interest-rate changes on **other** liabilities (lease liabilities, defined-benefit net liability, financing component of payables) | `FINANCING` | `PROVIDING_FINANCING_TO_CUSTOMERS` | IFRS 18 financing category |
| `IFRS18-SMBA-001` | Entity whose main business activity is investing in assets → related income/expenses classified operating instead of investing | `OPERATING` | `INVESTING_IN_ASSETS` | IFRS 18 specified main business activities |
| `IFRS18-SMBA-002` | Entity whose main business activity is providing financing to customers → related income/expenses classified operating instead of financing | `OPERATING` | `PROVIDING_FINANCING_TO_CUSTOMERS` | IFRS 18 specified main business activities |
| `IFRS18-LINKED-001` | FX differences classified in the same category as the item that gave rise to them | *inherited* | — | IFRS 18 FX requirement |
| `IFRS18-LINKED-002` | Gains/losses on derivatives and designated hedging instruments follow the category of the hedged item/risk | *inherited* | — | IFRS 18 derivatives requirement — **high uncertainty, see Q4** |
| `IFRS18-OPERATING-999` | Residual: everything else | `OPERATING` | — | IFRS 18 operating = residual |

`IFRS18-LINKED-*` rules cannot resolve in a single pass — they depend on another
line's outcome. Implementation: run steps 1–5 to a fixed point first, then
resolve linked items; a linked item whose underlying item cannot be identified
becomes `NEEDS_FACT` with a targeted question rather than a guess.

## 8. Confidence model (spec §11)

Confidence is **not** a probability that the answer is right. It is a routing
signal. Config-driven (`config/classification.yaml`, snapshotted per project):

```yaml
confidence:
  high_min:   0.95        # spec §11
  medium_min: 0.80
  # below medium_min → human review required
review:
  always_review_methods: ["AI", "UNRESOLVED"]
  always_review_if_changes_operating_profit_by_pct: 1.0
  materiality_floor_pct_of_revenue: 0.1
```

Assignment:

| Method | Confidence | Review required? |
|---|---|---|
| `RULE`, source = IFRS standard, no fact dependency | 1.00 | No |
| `RULE`, depended on a user-confirmed fact | 1.00 | No (the fact was reviewed) |
| `RULE`, source = secondary (capped by `confidence_ceiling`) | ≤ 0.90 | Yes |
| `RESIDUAL_DEFAULT`, account normalized confidently | 0.90 | No, unless material *and* previously outside operating |
| `RESIDUAL_DEFAULT`, account not normalized | 0.50 | **Yes** |
| `AI` | model's value | **Always yes** (spec §1) |
| `UNRESOLVED` | — | **Always yes**, blocks finalization |
| `USER` | 1.00 | Already reviewed |

**High AI confidence never bypasses review** (spec §1, §11). A rule at 1.00 does
not require review because a *human wrote and cited the rule*; the reviewable
artifact is the rule, exposed at `GET /api/rules`.

## 9. AI advisor contract (spec §12)

Strict structured output. Schema:

```json
{ "type": "object", "additionalProperties": false,
  "required": ["classification","confidence","reasoning","requires_human_review","evidence"],
  "properties": {
    "classification": { "enum": ["OPERATING","INVESTING","FINANCING",
                                 "INCOME_TAX","DISCONTINUED_OPERATION","UNCLASSIFIED"] },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
    "reasoning": { "type": "string", "maxLength": 1000 },
    "requires_human_review": { "type": "boolean" },
    "evidence": { "type": "array", "maxItems": 5, "items": {
        "type": "object", "additionalProperties": false,
        "required": ["type","reference"],
        "properties": {
          "type": { "enum": ["financial_statement","note","standard","other"] },
          "reference": { "type": "string", "maxLength": 200 } } } } } }
```

Handling:
1. Validate against the schema.
2. Invalid → one repair attempt (return the validation error), then one retry at
   temperature 0, then fail to `UNCLASSIFIED` + `requires_human_review = true`.
   **A failed AI call never silently becomes operating.**
3. `ai_raw_response` is persisted verbatim for audit even on success.
4. Server-side override: `requires_human_review` is forced to `true` regardless
   of what the model returns. The field is retained as a signal of the model's
   own uncertainty, not as a control.
5. Input is minimal and delimited (arch §6): account label, amount, section,
   current category, note excerpt — never the whole document.

## 10. Statement reconstruction

```python
def reconstruct(lines, decisions) -> Ifrs18Statement:
    # only non-subtotal lines participate
    buckets = group_by_category(lines, decisions)

    operating_profit = sum(buckets[OPERATING])
    investing_result = sum(buckets[INVESTING])
    financing_result = sum(buckets[FINANCING])
    tax             = sum(buckets[INCOME_TAX])
    discontinued    = sum(buckets[DISCONTINUED_OPERATION])

    pbfit            = operating_profit + investing_result
    profit_before_tax = pbfit + financing_result
    profit_continuing = profit_before_tax + tax          # tax is already negative
    profit_for_period = profit_continuing + discontinued
```

With the signed convention (arch §5) every subtotal is a plain sum — there is no
per-account sign logic anywhere, which removes the largest class of bug in tools
of this kind.

Required subtotals presented: `OPERATING_PROFIT`,
`PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES`, `PROFIT_FOR_THE_PERIOD` ⚠ VERIFY.
Whether `PROFIT_BEFORE_FINANCING_AND_INCOME_TAXES` is presented for entities with
a financing-to-customers main business activity is **open** — see Q3.

## 11. Worked example from spec §2 — and the inconsistency it reveals

Input (signed convention):

| Line | Amount |
|---|---|
| Revenue | +100,000 |
| Cost of sales | −70,000 |
| SG&A | −20,000 |
| Finance income | +3,000 |
| Finance costs | −2,000 |
| Other income | +1,000 |
| *Profit before tax (subtotal)* | *12,000* |

Sum of the six detail lines = **12,000** ✓ matches the reported subtotal.

Spec §2's IFRS 18 presentation shows:

```
Operating profit      10,000
Investment income      3,000
Finance costs         -2,000
Other / relevant …        ?
Profit before tax     12,000
```

`10,000 + 3,000 − 2,000 = 11,000 ≠ 12,000`. **The example as literally written
leaves ₩1,000 unplaced** — the Other income line. If Other income is operating,
operating profit is 11,000 and the statement reconciles at 12,000.

This is not a nitpick; it is precisely the failure mode the reconciliation gate
(arch §7) exists to catch, and it is why total invariance is an exact, blocking
check rather than a soft warning. Note also that "Finance income 3,000" was
relabelled "Investment income" — interest on cash and cash equivalents falls in
the **investing** category, which is a frequent source of confusion and is
exactly why the rule set names its source for every decision.

## 12. Test vectors (spec §26 step 6, §30)

Written before implementation. Each is a pure-function test on `domain/`.

### T1 — spec §30 baseline
```
Revenue +100, Operating expense -70, Interest income +10, Finance cost -5
⇒ OPERATING  = 100 - 70          =  30
  INVESTING  = +10                =  10   (interest on cash)
  FINANCING  = -5                 =  -5
  PBFIT      = 30 + 10            =  40
  PBT        = 40 - 5             =  35
```
Spec §30 states the expected operating profit is **30** ✓.

### T2 — reclassification changes operating profit, not PBT
Same data, but the entity previously reported interest income inside operating:
```
Reported operating profit  = 100 - 70 + 10 = 40
IFRS 18 operating profit   = 100 - 70      = 30
Δ operating profit         = -10
Δ PBT                      =   0     ← must be exactly zero
```
Spec §30 also mentions an expected transition "30 → 20". That does not follow
from the four figures given; the derivation above yields 40 → 30. **Flagged for
clarification** — see Q6. The assertion that Δ PBT is exactly 0 holds regardless
and is the more important invariant.

### T3 — SMBA flips the answer
Entity with `INVESTING_IN_ASSETS = true`: interest income stays operating.
```
OPERATING = 100 - 70 + 10 = 40 ;  INVESTING = 0 ;  PBT = 35 (unchanged)
```

### T4 — SMBA unknown
`INVESTING_IN_ASSETS = None` ⇒ interest income is `UNRESOLVED`, a
`review_questions` row exists, `can_finalize == false`, and `/finalize` returns
`422`.

### T5 — subtotal lines are never double counted
Input includes 매출총이익 30 as `is_subtotal`. Category sums must ignore it;
PBT must be 35, not 65.

### T6 — sign and zero handling
Zero-amount line classifies normally and contributes 0. A negative revenue
(sales return) stays `OPERATING_REVENUE`. An income-statement line whose sign
contradicts `normalized_accounts.default_nature` raises an extraction warning,
not a classification change.

### T7 — duplicate account names
Two lines both labelled 기타수익 with different amounts are classified
independently; neither is merged.

### T8 — rounding
Source stated in 백만원 with a 0.5 rounding difference between the sum of details
and the printed subtotal: PBT check passes within tolerance, and the residual
difference is reported in `reconciliation_details`, never absorbed silently.

### T9 — user override wins and is recorded
AI proposes `INVESTING` at 0.94; user sets `OPERATING`. Final category is
`OPERATING`, `method == USER`, `user_override == true`, one `user_reviews` row
and one `audit_logs` row exist, and re-running `/classify` with
`preserve_user_overrides: true` does not revert it.

### T10 — reconciliation failure blocks
Inject an item with no category. `/finalize` returns `409`, project status is
`RECONCILIATION_FAILED`, `/impact` still responds but with
`reconciliation_status: "FAILED"`, and unwatermarked export is refused.

### T11 — classification conflict
Two active rules match one line with different categories ⇒ rule-set validation
fails at load; the engine refuses to start rather than picking one.

### T12 — multiple periods
Current and comparative columns are separate `financial_statements`; classifying
one does not alter the other; impact is computed per period.
