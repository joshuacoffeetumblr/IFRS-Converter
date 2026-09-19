"""Product metadata that must not be hardcoded in the frontend.

The disclaimer required by spec §24 is served by the API so it cannot be lost in
a UI refactor and is necessarily present in every export.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["meta"])

DISCLAIMER_EN = (
    "This analysis is an IFRS 18 classification and impact analysis tool. "
    "It does not constitute accounting advice or an authoritative determination "
    "of IFRS compliance."
)

DISCLAIMER_KO = (
    "본 분석은 IFRS 18 분류 및 영향 분석 도구입니다. "
    "회계자문이나 IFRS 준수 여부에 대한 권위 있는 판단을 구성하지 않습니다."
)

#: Scope limitations stated in-product rather than silently omitted
#: (open question Q7, resolved 2026-09-19).
LIMITATIONS_EN = (
    "Restructures the statement of profit or loss only.",
    "Does not produce management-defined performance measure (MPM) disclosures.",
    "Does not restructure other comprehensive income.",
    "Does not apply IFRS 18 aggregation and disaggregation requirements.",
    "Validated against non-financial corporates; not validated for banks, "
    "insurers or securities firms.",
)


class DisclaimerResponse(BaseModel):
    disclaimer_en: str
    disclaimer_ko: str
    limitations_en: list[str]


@router.get("/meta/disclaimer", response_model=DisclaimerResponse)
async def disclaimer() -> DisclaimerResponse:
    return DisclaimerResponse(
        disclaimer_en=DISCLAIMER_EN,
        disclaimer_ko=DISCLAIMER_KO,
        limitations_en=list(LIMITATIONS_EN),
    )
