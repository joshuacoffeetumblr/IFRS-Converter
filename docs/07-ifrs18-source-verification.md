# IFRS 18 Source Verification Log

> Spec §25 requires the basis for each rule to be recorded before it is coded,
> with authority ranked: (1) IFRS Foundation / IASB, (2) official Taxonomy,
> (3) official educational material, (4) Big 4 technical publications, (5) other.

## How this was verified, and the limitation

**Verified 2026-09-19 via web search of IFRS Foundation and Big 4 publications.**

**Limitation, stated plainly:** the development environment's network policy
blocks direct access to `ifrs.org`, `assets.kpmg.com`, `ifrscommunity.com` and
similar domains, so the pages themselves could not be retrieved and quoted. The
findings below come from **search-engine summaries of those sources**, which is
better than unaided recall but is still second-hand. Paragraph numbers are
recorded so a reader with access to the issued standard can confirm each one
against the primary text.

Accordingly every rule below is marked `VERIFIED-SECONDARY`, not `VERIFIED`. A
final check against the issued text of IFRS 18 remains an open task; see
"Remaining verification" at the end.

## Standard identification

| Item | Finding |
|---|---|
| Standard | IFRS 18 *Presentation and Disclosure in Financial Statements* |
| Issued | April 2024 |
| Effective | Annual reporting periods beginning on or after **1 January 2027**; earlier application permitted |
| Replaces | IAS 1 |

## Verified findings

### F1 — Where the classification requirements live
Paragraphs **47–68** and **B42–B76** contain the requirements for classifying
income and expenses into categories. Paragraphs **52–68** set out classification
into the operating, investing, financing, income taxes and discontinued
operations categories. Paragraphs **49–50** address classification in the
investing or financing category.

### F2 — Operating is the residual category ✅ confirms the core design
The operating category captures all income and expenses that do not belong to
the investing, financing, income taxes or discontinued operations categories.
Investing and financing are determined first; what remains is operating.

This confirms the central architectural decision (`01-architecture.md` §2): the
rule engine positively identifies investing, financing, tax and discontinued
items only.

### F3 — Equity-method associates and joint ventures ⚠️ CORRECTS AN EARLIER DRAFT
Income and expenses from investments in **associates, joint ventures and
unconsolidated subsidiaries accounted for using the equity method are always
classified in the investing category — regardless of the entity's business
model**, and even if they arise from the entity's main business activities.
There is no specified-main-business-activity exception for these.

**This corrects the draft rule set in `04-classification-engine.md` §7**, where
`IFRS18-SMBA-001` implied that an entity whose main business activity is
investing in assets would reclassify equity-method results to operating. It
would not. The corrected rule set records this as an unconditional rule.

This matters materially for Korean corporates, where 지분법손익 is common and is
usually presented below 영업이익 today.

### F4 — Other assets in the investing category
Income and expenses from other assets belong in investing if the asset generates
a return **individually and largely independently of the entity's other
resources** — typically debt and equity investments and investment property.
Cash and cash equivalents are dealt with separately from that assessment.

### F5 — Foreign exchange differences → **paragraph B65**
An entity classifies foreign exchange differences recognised in profit or loss
applying IAS 21 **in the same category as the income and expenses from the items
that gave rise to them**. Where that would involve **undue cost or effort**, the
differences are classified in the **operating** category.

Worked consequence cited in the sources: FX on a trade receivable arising from
the sale of goods or services is classified in **operating**.

### F6 — Derivatives and hedging instruments → **paragraph B72**
Gains and losses on a derivative, and on a financial instrument designated as a
hedging instrument, are classified **in the same category as the income and
expenses affected by the risks the instrument is used to manage**. This covers
both designated hedging instruments and derivatives not designated under IFRS 9
but used to manage identified risks.

Where applying that would require **grossing up** gains and losses, or involve
**undue cost or effort**, the entity classifies **all** gains and losses on the
derivative in the **operating** category.

The operative step is identifying *which risk* the derivative manages — that
determines the category.

### F7 — IFRIC Agenda Decision, April 2026: derivative managing an FX exposure
Published April 2026 (addendum to IFRIC Update March 2026); no IASB member
objected. Facts: a group nets a foreign-currency asset exposure (loan receivable,
investing) against a larger foreign-currency liability exposure (loan payable,
financing), leaving a net liability exposure, and hedges the net exposure with
an external forward.

Conclusion: because the external derivative manages the FX risk of the **net
liability exposure**, applying **B72** the parent classifies the gain or loss on
that derivative in the **financing** category — unless doing so would require
grossing up or involve undue cost or effort.

### F8 — IFRIC Agenda Decision, April 2026: FX difference on an intragroup monetary item
A second April 2026 agenda decision addresses classification of a foreign
exchange difference arising from an intragroup monetary liability or asset.
Recorded here as relevant to consolidated Korean groups; its detailed conclusion
was not retrievable in this environment and is listed under remaining
verification.

### F9 — Required subtotals, and the prohibition → **paragraph 73**
Required subtotals: **operating profit or loss**, **profit or loss before
financing and income taxes**, and **profit or loss**.

Both operating profit and profit before financing and income taxes are presented
**even when they are the same amount** (an entity with no investing items still
presents both).

**Paragraph 73** is the exception, and it is a **prohibition, not an exemption**:
an entity whose specified main business activity is providing financing to
customers, and which classifies in the operating category the interest expense
on liabilities *unrelated* to providing financing to customers, is **not
permitted** to present the "profit or loss before financing and income taxes"
subtotal.

Such an entity may present an additional subtotal under **paragraph 24** if that
gives a useful structured summary, but **must not label it in a way that implies
financing amounts are excluded** — "profit before financing" would be misleading
and is not allowed.

This is narrower and stricter than the hypothesis recorded in Q3, which guessed
at a general "not required for financial institutions" carve-out.

### F10 — Specified main business activities → **paragraph B30**
IFRS 18 specifies two: **investing in assets** and **providing financing to
customers**. Paragraph **B30** confirms an entity may have **more than one** main
business activity. Entities with these activities classify in operating certain
income and expenses that would otherwise be investing or financing.

## Corrected rule set

| rule_id | Requirement | Category | Fact required | Citation | Status |
|---|---|---|---|---|---|
| `IFRS18-TAX-001` | Income tax expense/income | `INCOME_TAX` | — | IFRS 18 paras 52–68 | VERIFIED-SECONDARY |
| `IFRS18-DISC-001` | Discontinued operations | `DISCONTINUED_OPERATION` | — | IFRS 18 paras 52–68; IFRS 5 | VERIFIED-SECONDARY |
| `IFRS18-INVESTING-001` | Equity-method associates, JVs, unconsolidated subsidiaries — **unconditional, no SMBA exception** | `INVESTING` | **none** ⚠️ corrected | IFRS 18 paras 49–50 (F3) | VERIFIED-SECONDARY |
| `IFRS18-INVESTING-002` | Cash and cash equivalents | `INVESTING` | `INVESTING_IN_ASSETS` | IFRS 18 paras 49–50 | VERIFIED-SECONDARY |
| `IFRS18-INVESTING-003` | Other assets generating a return individually and largely independently | `INVESTING` | `INVESTING_IN_ASSETS` | IFRS 18 paras 49–50 (F4) | VERIFIED-SECONDARY |
| `IFRS18-FINANCING-001` | Liabilities from transactions involving only the raising of finance | `FINANCING` | `PROVIDING_FINANCING_TO_CUSTOMERS` | IFRS 18 paras 52–68 | VERIFIED-SECONDARY |
| `IFRS18-FINANCING-002` | Interest on other liabilities (leases, defined benefit, financing component of payables) | `FINANCING` | `PROVIDING_FINANCING_TO_CUSTOMERS` | IFRS 18 paras 52–68 | VERIFIED-SECONDARY |
| `IFRS18-SMBA-001` | Investing in assets is a main business activity → related investing income/expenses to operating. **Does not extend to equity-method results (F3).** | `OPERATING` | `INVESTING_IN_ASSETS` | IFRS 18 B30 | VERIFIED-SECONDARY |
| `IFRS18-SMBA-002` | Providing financing to customers is a main business activity → related financing income/expenses to operating | `OPERATING` | `PROVIDING_FINANCING_TO_CUSTOMERS` | IFRS 18 B30 | VERIFIED-SECONDARY |
| `IFRS18-FX-001` | FX differences follow the category of the item that gave rise to them | *inherited* | — | **IFRS 18 B65** (F5) | VERIFIED-SECONDARY |
| `IFRS18-FX-002` | FX differences → operating where inheritance involves undue cost or effort | `OPERATING` | user assertion | **IFRS 18 B65** (F5) | VERIFIED-SECONDARY |
| `IFRS18-DERIV-001` | Derivative / designated hedging instrument follows the category of the risk managed | *inherited* | risk identification | **IFRS 18 B72** (F6, F7) | VERIFIED-SECONDARY |
| `IFRS18-DERIV-002` | All gains/losses → operating where B72 would require grossing up or undue cost or effort | `OPERATING` | user assertion | **IFRS 18 B72** (F6) | VERIFIED-SECONDARY |
| `IFRS18-SUBTOTAL-001` | Present operating profit, PBFIT and profit or loss — PBFIT even when equal to operating profit | — | — | IFRS 18 para 69 (F9) | VERIFIED-SECONDARY |
| `IFRS18-SUBTOTAL-002` | PBFIT **prohibited** where para 73 applies; additional subtotal under para 24 must not imply financing is excluded | — | `PROVIDING_FINANCING_TO_CUSTOMERS` + operating classification of non-customer interest expense | **IFRS 18 para 73, para 24** (F9) | VERIFIED-SECONDARY |
| `IFRS18-OPERATING-999` | Residual: everything else | `OPERATING` | — | IFRS 18 paras 52–68 (F2) | VERIFIED-SECONDARY |

## Remaining verification

Requires access to the issued text of IFRS 18:

1. Exact wording and paragraph numbers of F1–F10 against the standard.
2. The precise condition in **paragraph 73** — in particular whether the
   prohibition is triggered by the classification election alone, and how an
   entity that has *not* made that election is treated.
3. The detailed conclusion of the April 2026 agenda decision on **intragroup FX
   differences** (F8).
4. Whether `IFRS18-INVESTING-002` (cash and cash equivalents) is genuinely
   conditional on the `INVESTING_IN_ASSETS` activity, or conditional on a
   different SMBA test. F4 notes cash is assessed separately from other assets;
   the interaction with B30 was not resolved here.
5. Whether `IFRS18-FINANCING-002` is conditional on an SMBA at all.

Items 4 and 5 affect rules that fire on ordinary non-financial corporates, so
they matter for the MVP. Items 2 and 3 affect entities outside MVP scope.

## Sources

- [IFRS 18 — IFRS Foundation](https://www.ifrs.org/issued-standards/list-of-standards/ifrs-18-presentation-and-disclosure-in-financial-statements/)
- [Agenda Decision: Classification of Gains and Losses on a Derivative Managing a Foreign Currency Exposure (IFRS 18), April 2026](https://www.ifrs.org/content/dam/ifrs/supporting-implementation/agenda-decisions/2026/classification-of-gains-losses-on-derivative-managing-fc-exposure-apr-26.pdf)
- [Agenda Decision: Classification of a Foreign Exchange Difference from an Intragroup Monetary Liability (or Asset) (IFRS 18)](https://www.ifrs.org/content/dam/ifrs/supporting-implementation/agenda-decisions/2026/classification-of-fx-diff-from-intragroup-monetary-liability-or-asset-apr-26.pdf)
- [IASB Update April 2026](https://www.ifrs.org/news-and-events/updates/iasb/2026/iasb-update-april-2026/)
- [IFRS 18 Effect Analysis, April 2024 — IFRS Foundation](https://www.ifrs.org/content/dam/ifrs/publications/amendments/english/2024/effect-analysis-ifrs18-april2024.pdf)
- [KPMG, First Impressions: Presentation and disclosure — IFRS 18](https://assets.kpmg.com/content/dam/kpmgsites/xx/pdf/ifrg/2024/isg-first-impressions-presentation-and-disclosure-ifrs-18.pdf)
- [PwC Viewpoint — IFRS 18: Insights for financial services companies](https://viewpoint.pwc.com/dt/gx/en/pwc/in_briefs/in_briefs_INT/in_briefs_INT/ifrs18insights.html)
- [PwC Viewpoint — IFRS 18: Key treasury topics for corporate entities](https://viewpoint.pwc.com/dt/gx/en/pwc/in_briefs/in_briefs_INT/in_briefs_INT/ifrs-18-key-treasury-topics-for-corporate-entities.html)
- [EY — Applying IFRS 18, Appendix D: Additional considerations for banks](https://www.ey.com/content/dam/ey-unified-site/ey-com/en-gl/technical/ifrs-technical-resources/documents/ey-gl-apply-ifrs-18-appendix-additional-considerations-for-banks-v2-12-2025.pdf)
- [BDO — IFRS 18: Financing category differences for entities with specified main business activities](https://www.bdo.com.au/en-au/blogs/corporate-reporting/ifrs-18-financing-category-differences-for-entities-with-specified-main-business-activities)
- [BDO — Disaggregating foreign exchange differences in the IFRS 18 statement of profit or loss](https://www.bdo.com.au/en-au/blogs/corporate-reporting/disaggregating-foreign-exchange-differences-in-the-ifrs-18-statement-of-profit-or-loss)
- [Forvis Mazars — Presentation of exchange differences under IFRS 18](https://www.forvismazars.com/uk/en/insights/financial-and-corporate-reporting-updates/presentation-of-exchange-differences-under-ifrs-18)
- [RSM UK — IFRS 18: how to classify income and expenses](https://www.rsmuk.com/insights/bridging-the-gaap/ifrs-18-how-to-classify-income-and-expenses)
- [ESMA — Reshaping performance: Implementation of IFRS 18](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA32-193237008-9180_Public_Statement_IFRS_18.pdf)
