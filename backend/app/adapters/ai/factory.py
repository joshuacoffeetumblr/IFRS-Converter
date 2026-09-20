"""Building the advisors, or deciding there are none.

One place decides whether the AI layer exists in a given run, so no caller has
to reason about keys, and no code path can accidentally half-enable it: either
both advisors are real or both are absent, and absent is the default.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.ai.advisor import (
    AdvisorConfig,
    AnthropicAccountAdvisor,
    AnthropicClassificationAdvisor,
)
from app.core.config import Settings
from app.core.logging import get_logger
from app.data.catalog import load_catalog
from app.domain.classification import ClassificationAdvisor
from app.domain.normalization import AccountAdvisor

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Advisors:
    """Both advisors, or neither."""

    classification: ClassificationAdvisor | None = None
    account: AccountAdvisor | None = None

    @property
    def available(self) -> bool:
        return self.classification is not None


def build_advisors(settings: Settings) -> Advisors:
    """The advisors this configuration supports.

    Returns nothing at all when no key is configured, when the SDK is not
    installed, or when the assistant is switched off. That is not an error
    state: the product's answer for anything a rule cannot decide is a person,
    with or without AI.
    """
    if not settings.ai.configured:
        return Advisors()

    try:
        import anthropic
    except ImportError:  # pragma: no cover - depends on the install
        log.info("ai_sdk_missing")
        return Advisors()

    # The SDK reads ANTHROPIC_API_KEY itself, so an explicit key is passed
    # only when this product was configured with its own.
    client = (
        anthropic.Anthropic(api_key=settings.ai.api_key)
        if settings.ai.api_key
        else anthropic.Anthropic()
    )
    config = AdvisorConfig(
        model=settings.ai.model,
        effort=settings.ai.effort,
        max_tokens=settings.ai.max_tokens,
        timeout_seconds=settings.ai.timeout_seconds,
        repair_attempts=settings.ai.repair_attempts,
    )

    _, definitions = load_catalog()
    codes = tuple(item.code for item in definitions)

    log.info("ai_enabled", model=config.model, effort=config.effort, accounts=len(codes))
    return Advisors(
        classification=AnthropicClassificationAdvisor(client, config),
        account=AnthropicAccountAdvisor(client, config, codes),
    )
