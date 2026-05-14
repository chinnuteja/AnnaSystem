"""Parallel catalog search across mock (later: real) commerce providers.

Retrieval-first routing uses these hits instead of hardcoded dish lists.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from packages.providers.interface import CanonicalSKU, Location
from packages.providers.router import provider_router

logger = logging.getLogger("foodleaf.catalog_search")


@dataclass(frozen=True)
class CatalogHit:
    """One scored match from a provider catalog."""

    domain: str  # grocery | food_delivery | dineout
    score: float  # 0.0 - 1.0
    label: str  # human-readable label for debugging / evidence
    detail: str  # short description


def _tokenize(q: str) -> list[str]:
    return [t for t in re.split(r"\W+", q.lower()) if len(t) >= 2]

def _split_multi_item_query(q: str) -> list[str]:
    """Split 'milk and peanut butter' -> ['milk', 'peanut butter'].

    This is intentionally simple; real production uses embeddings + entity extraction.
    """
    s = q.strip().lower()
    if not s:
        return []
    # Normalize common joiners
    s = s.replace("&", " and ")
    s = re.sub(r"\s+", " ", s)
    parts = re.split(r"\b(?:and|plus|with)\b|,", s)
    out: list[str] = []
    for p in parts:
        p = " ".join(p.split()).strip()
        if len(p) >= 3:
            out.append(p)
    # If nothing split, keep original
    return out or [s]


def _score_text(haystack: str, query: str) -> float:
    """Cheap lexical score: substring + token overlap."""
    if not query or not haystack:
        return 0.0
    h = haystack.lower()
    q = query.lower().strip()
    if not q:
        return 0.0
    if q in h:
        return 1.0
    q_tokens = set(_tokenize(q))
    if not q_tokens:
        return 0.0
    h_tokens = set(_tokenize(h))
    overlap = q_tokens & h_tokens
    if overlap:
        return 0.55 + 0.1 * min(len(overlap), 4)
    # fuzzy: any query token substring in haystack
    for t in q_tokens:
        if len(t) >= 3 and t in h:
            return 0.45
    return 0.0


async def search_grocery_hits(query: str, language: str, location: Location, limit: int = 8) -> list[CatalogHit]:
    g = provider_router.grocery()
    # Multi-item queries like "milk and peanut butter" won't match a single SKU string.
    # Split and search each subquery, then merge.
    subqs = _split_multi_item_query(query)
    if not subqs:
        return []

    # Keep per-subquery limit small so we don't blow up latency.
    per_q = max(1, min(3, limit))
    tasks = [g.search_skus(sq, language, location, limit=per_q) for sq in subqs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    dedup: dict[str, CanonicalSKU] = {}
    for res in results:
        if isinstance(res, BaseException):
            logger.warning("grocery search_skus failed: %s", res)
            continue
        for sku in res:
            dedup[sku.canonical_key] = sku

    hits: list[CatalogHit] = []
    for sku in dedup.values():
        blob = f"{sku.display_name} {sku.brand} {sku.category} {sku.subcategory}"
        # Score against best-matching subquery
        sc = max((_score_text(blob, sq) for sq in subqs), default=0.0)
        if sc <= 0.0 and query.strip():
            sc = 0.28  # weak prior: provider search returned it
        hits.append(
            CatalogHit(
                domain="grocery",
                score=min(1.0, sc),
                label=sku.display_name,
                detail=f"{sku.brand} · {sku.pack_size}",
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


async def search_food_hits(query: str, location: Location, limit: int = 12) -> list[CatalogHit]:
    """Search restaurant names/cuisines and menu item names (mock catalog)."""
    q = query.strip()
    if not q:
        return []

    food = provider_router.food()
    hits: list[CatalogHit] = []
    seen: set[str] = set()

    rests_q = await food.search_restaurants(q, None, location, None, False, limit=10)
    for r in rests_q:
        blob = f"{r.name} {' '.join(r.cuisines)}"
        sc = _score_text(blob, q)
        if sc <= 0:
            continue
        key = f"r:{r.provider_restaurant_id}"
        seen.add(key)
        hits.append(
            CatalogHit(
                domain="food_delivery",
                score=min(1.0, sc),
                label=r.name,
                detail=", ".join(r.cuisines[:3]),
            )
        )

    rests_all = await food.search_restaurants(None, None, location, None, False, limit=8)
    for r in rests_all:
        try:
            menu = await food.get_restaurant_menu(r.provider_restaurant_id)
        except Exception:
            continue
        for item in menu[:40]:
            sc = _score_text(item.name, q)
            if item.description:
                sc = max(sc, _score_text(item.description, q) * 0.9)
            if sc <= 0:
                continue
            key = f"m:{r.provider_restaurant_id}:{item.provider_menu_item_id}"
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                CatalogHit(
                    domain="food_delivery",
                    score=min(1.0, sc + 0.05),
                    label=f"{item.name} ({r.name})",
                    detail=item.category or "menu",
                )
            )
        if len(hits) >= limit:
            break

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


async def search_dineout_hits(query: str, location: Location, limit: int = 8) -> list[CatalogHit]:
    d = provider_router.dineout()
    hits: list[CatalogHit] = []
    q = query.strip()
    if not q:
        return hits

    rests = await d.search_dineout(q, None, location, False, limit=10)
    for r in rests:
        blob = f"{r.name} {' '.join(r.cuisines)}"
        sc = _score_text(blob, q)
        if sc <= 0:
            continue
        hits.append(
            CatalogHit(
                domain="dineout",
                score=min(1.0, sc),
                label=r.name,
                detail=", ".join(r.cuisines[:3]),
            )
        )

    if not hits:
        rests_b = await d.search_dineout(None, None, location, False, limit=8)
        for r in rests_b:
            blob = f"{r.name} {' '.join(r.cuisines)}"
            sc = _score_text(blob, q)
            if sc <= 0:
                continue
            hits.append(
                CatalogHit(
                    domain="dineout",
                    score=min(1.0, sc),
                    label=r.name,
                    detail=", ".join(r.cuisines[:3]),
                )
            )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


async def parallel_catalog_search(
    query: str,
    language: str,
    location: Location,
    *,
    grocery_limit: int = 8,
    food_limit: int = 12,
    dineout_limit: int = 8,
) -> dict[str, list[CatalogHit]]:
    """Run grocery, food, dineout searches concurrently."""
    grocery_task = search_grocery_hits(query, language, location, grocery_limit)
    food_task = search_food_hits(query, location, food_limit)
    dine_task = search_dineout_hits(query, location, dineout_limit)
    raw = await asyncio.gather(grocery_task, food_task, dine_task, return_exceptions=True)

    def _as_hits(res, domain: str) -> list[CatalogHit]:
        if isinstance(res, BaseException):
            logger.warning("catalog_search %s failed: %s", domain, res)
            return []
        return res  # type: ignore[return-value]

    return {
        "grocery": _as_hits(raw[0], "grocery"),
        "food_delivery": _as_hits(raw[1], "food_delivery"),
        "dineout": _as_hits(raw[2], "dineout"),
    }
