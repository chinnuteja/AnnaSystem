from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_DIR))

from app.agents.catalog_search import CatalogHit, parallel_catalog_search, _score_text
from app.agents.semantic_router import apply_route_to_intent, route_domains
from app.schemas.message import ParsedIntent, ParsedItem
from packages.providers.interface import Location


def test_score_text_substring_and_tokens():
    assert _score_text("Aashirvaad Whole Wheat Atta 10kg", "atta") >= 0.9
    assert _score_text("Egg Hakka Noodles", "egg noodles") >= 0.5


@pytest.fixture
def hyderabad():
    return Location(
        latitude=17.4486,
        longitude=78.3792,
        pincode="500032",
        city="Hyderabad",
    )


def test_parallel_catalog_search_returns_three_domains(hyderabad):
    async def run():
        t0 = time.monotonic()
        out = await parallel_catalog_search("atta 10kg", "te-IN", hyderabad)
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert "grocery" in out and "food_delivery" in out and "dineout" in out
        assert isinstance(elapsed_ms, float)
        assert out["grocery"], "mock grocery catalog should match atta"
        top_g = max(h.score for h in out["grocery"])
        assert top_g >= 0.2

    asyncio.run(run())


def test_route_domains_clear_grocery_win(monkeypatch, hyderabad):
    from app.agents import semantic_router as sr

    async def fake_parallel(*_a, **_k):
        return {
            "grocery": [CatalogHit("grocery", 0.92, "Atta 10kg", "brand")],
            "food_delivery": [CatalogHit("food_delivery", 0.25, "Random", "x")],
            "dineout": [],
        }

    monkeypatch.setattr(sr, "parallel_catalog_search", fake_parallel)

    async def _run():
        intent = ParsedIntent(
            action="ORDER",
            raw_text="Aashirvaad atta 10kg",
            items=[ParsedItem(text="atta 10kg", quantity=1)],
            goal="shop",
            domain_hint="unknown",
        )
        r = await route_domains(intent, location=hyderabad, language="te-IN")
        assert r.chosen == "grocery"
        assert r.domain_scores["grocery"] == 0.92
        merged = apply_route_to_intent(intent, r)
        assert merged.domain_hint == "grocery"
        assert merged.router_trace is not None

    asyncio.run(_run())


def test_route_domains_ambiguous_when_close(monkeypatch, hyderabad):
    from app.agents import semantic_router as sr

    async def fake_parallel(*_a, **_k):
        return {
            "grocery": [CatalogHit("grocery", 0.45, "Ice cream tub", "frozen")],
            "food_delivery": [CatalogHit("food_delivery", 0.44, "Ice cream dessert", "menu")],
            "dineout": [],
        }

    monkeypatch.setattr(sr, "parallel_catalog_search", fake_parallel)

    async def _run():
        intent = ParsedIntent(
            action="ORDER",
            raw_text="ice cream",
            items=[],
            goal="shop",
            domain_hint="unknown",
        )
        r = await route_domains(intent, location=hyderabad, language="te-IN")
        assert r.chosen == "ambiguous"
        merged = apply_route_to_intent(intent, r)
        assert merged.needs_clarification is True

    asyncio.run(_run())


def test_track_short_circuits_catalog(monkeypatch, hyderabad):
    from app.agents import semantic_router as sr

    async def boom(*_a, **_k):
        raise AssertionError("catalog should not run for TRACK")

    monkeypatch.setattr(sr, "parallel_catalog_search", boom)

    async def _run():
        intent = ParsedIntent(
            action="TRACK",
            raw_text="order status",
            items=[],
            goal="track",
            domain_hint="unknown",
        )
        r = await route_domains(intent, location=hyderabad, language="te-IN")
        assert r.status_score == 1.0

    asyncio.run(_run())
