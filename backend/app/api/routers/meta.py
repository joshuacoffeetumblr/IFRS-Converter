"""Product metadata that must not be hardcoded in the frontend.

The disclaimer required by spec §24 is served by the API so it cannot be lost
in a UI refactor and is necessarily present wherever the product speaks. It and
the scope limitations live in ``app.core.product``, which the Excel exporter
reads too — one copy, so an export cannot quietly claim more than the screen.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.product import DISCLAIMER_EN, DISCLAIMER_KO, LIMITATIONS_EN

router = APIRouter(tags=["meta"])


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
