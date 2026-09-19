"""Product text that must appear identically everywhere it is shown.

Spec §24 requires the disclaimer on screen **and** in every export, and open
question Q7 requires the scope limitations to be stated rather than silently
omitted. Keeping one copy is how that is guaranteed: a second copy is a second
thing to forget to update, and the failure mode — an export that quietly claims
more than the product does — is exactly the one worth engineering against.
"""

from __future__ import annotations

DISCLAIMER_EN = (
    "This analysis is an IFRS 18 classification and impact analysis tool. "
    "It does not constitute accounting advice or an authoritative determination "
    "of IFRS compliance."
)

DISCLAIMER_KO = (
    "본 분석은 IFRS 18 분류 및 영향 분석 도구입니다. "
    "회계자문이나 IFRS 준수 여부에 대한 권위 있는 판단을 구성하지 않습니다."
)

#: Stated in-product rather than silently omitted (open question Q7).
LIMITATIONS_EN: tuple[str, ...] = (
    "Restructures the statement of profit or loss only.",
    "Does not produce management-defined performance measure (MPM) disclosures.",
    "Does not restructure other comprehensive income.",
    "Does not apply IFRS 18 aggregation and disaggregation requirements.",
    "Validated against non-financial corporates; not validated for banks, "
    "insurers or securities firms.",
    "IFRS 18 citations are verified against IFRS Foundation and Big 4 "
    "publications, not against the issued text of the standard.",
)

#: Applied to every sheet of an export produced from a statement that did not
#: reconcile (spec §19). Such a file must never be mistakable for a normal one.
UNRECONCILED_WATERMARK = "UNRECONCILED — DO NOT RELY ON THESE FIGURES"
