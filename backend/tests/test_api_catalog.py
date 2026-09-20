"""The rule set, the dictionary and the thresholds, over HTTP (spec §23, §25).

Explainability is not only a per-line "why?". A reviewer has to be able to
audit the engine itself — and to see how far each citation has actually been
verified, rather than being told to assume.
"""

from __future__ import annotations

from httpx import AsyncClient

from app.data.rule_catalog import load_rules


async def test_the_rule_set_is_served_in_the_order_it_is_applied(
    client: AsyncClient,
) -> None:
    """First match wins, so any other order would misdescribe the engine."""
    response = await client.get("/api/rules")

    assert response.status_code == 200
    body = response.json()
    priorities = [item["priority"] for item in body["items"]]
    assert priorities == sorted(priorities)
    assert body["items"][-1]["is_residual"] is True


async def test_every_rule_carries_a_citation_and_its_verification_status(
    client: AsyncClient,
) -> None:
    """Spec §25: the basis for a rule is recorded before the rule is coded."""
    body = (await client.get("/api/rules")).json()

    for rule in body["items"]:
        assert rule["source_reference"].strip()
        assert rule["verification_status"] in {
            "VERIFIED_PRIMARY",
            "VERIFIED_SECONDARY",
            "UNVERIFIED",
        }
        assert rule["description"].strip()
        assert rule["condition"]


async def test_the_standard_it_implements_is_named(client: AsyncClient) -> None:
    body = (await client.get("/api/rules")).json()

    assert body["standard"]["name"].startswith("IFRS 18")
    assert body["standard"]["mandatory_from"].startswith("2027")
    assert body["standard"]["early_application_permitted"] is True
    assert body["version"]


async def test_the_rules_that_need_a_fact_say_which(client: AsyncClient) -> None:
    body = (await client.get("/api/rules")).json()

    by_id = {item["rule_id"]: item for item in body["items"]}
    assert by_id["IFRS18-INVESTING-002"]["requires_activity_fact"] == "INVESTING_IN_ASSETS"
    assert by_id["IFRS18-DERIV-001"]["requires_line_fact"] == "DERIVATIVE_RISK_MANAGED"


async def test_the_served_rules_are_the_rules_the_engine_runs(client: AsyncClient) -> None:
    """Served from the same file, never from a second copy."""
    _, _, rules = load_rules()

    body = (await client.get("/api/rules")).json()

    assert {item["rule_id"] for item in body["items"]} == {rule.rule_id for rule in rules}


async def test_the_account_dictionary_is_served(client: AsyncClient) -> None:
    response = await client.get("/api/accounts")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(body["items"])
    codes = {item["code"] for item in body["items"]}
    assert {"REVENUE", "COST_OF_SALES", "INTEREST_INCOME"} <= codes


async def test_accounts_can_be_searched_by_caption(client: AsyncClient) -> None:
    body = (await client.get("/api/accounts", params={"q": "이자수익"})).json()

    assert body["items"]
    assert any(item["code"] == "INTEREST_INCOME" for item in body["items"])


async def test_an_aggregate_account_is_marked_as_one(client: AsyncClient) -> None:
    """These always reach human review, so a client has to be able to see it."""
    body = (await client.get("/api/accounts", params={"q": "기타수익"})).json()

    other_income = next(item for item in body["items"] if item["code"] == "OTHER_INCOME")
    assert other_income["ambiguous_by_default"] is True


async def test_the_active_thresholds_are_published(client: AsyncClient) -> None:
    """Spec §11: a user can see what the engine decided under."""
    response = await client.get("/api/config/classification")

    assert response.status_code == 200
    body = response.json()
    assert body["high_confidence_min"] == "0.9500"
    assert body["medium_confidence_min"] == "0.8000"
    assert body["rule_set_version"] and body["catalog_version"]


async def test_total_invariance_has_no_tolerance_and_says_so(client: AsyncClient) -> None:
    """If the sum of all income and expenses changes, the software has a bug —
    and no tolerance can make that acceptable."""
    body = (await client.get("/api/config/classification")).json()

    assert body["total_invariance_tolerance"] == "0.000000"


async def test_the_engine_itself_is_public(client: AsyncClient) -> None:
    """No statement data is involved, so no token is needed to read the rules.

    The alternative — making the standard's own logic reachable only with an
    account — would work against the explainability the product is built on.
    """
    for path in ("/api/rules", "/api/accounts", "/api/config/classification"):
        assert (await client.get(path)).status_code == 200
