from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import and_, desc, select

from packages.core.db import get_session
from packages.core.models import CanonicalSKU as DBCanonicalSKU, ProviderSKUMapping
from packages.providers.interface import Location, SkuPreview
from packages.providers.router import provider_router


PROVIDER = "swiggy_instamart_mcp"


async def find_substitutes(
    *,
    original_sku_id: str,
    category: str,
    limit: int = 2,
    in_stock_only: bool = True,
) -> list[SkuPreview]:
    stmt = _base_stmt().where(DBCanonicalSKU.category == category).where(
        and_(
            DBCanonicalSKU.canonical_key != original_sku_id,
            ProviderSKUMapping.provider_sku_id != original_sku_id,
        )
    )
    return await _previews_from_stmt(stmt, limit=limit, in_stock_only=in_stock_only)


async def find_options_in_category(
    *,
    category: str | None,
    limit: int = 3,
    in_stock_only: bool = True,
) -> list[SkuPreview]:
    stmt = _base_stmt()
    if category:
        stmt = stmt.where(DBCanonicalSKU.category == category)
    return await _previews_from_stmt(stmt, limit=limit, in_stock_only=in_stock_only)


async def find_alternative_pack(
    *,
    base_canonical_key_prefix: str,
    target_grams: int | None,
) -> SkuPreview | None:
    stmt = _base_stmt().where(DBCanonicalSKU.canonical_key.ilike(f"{base_canonical_key_prefix}%"))
    previews = await _previews_from_stmt(stmt, limit=12, in_stock_only=True)
    if not previews:
        return None
    if target_grams is None:
        return previews[0]
    return min(previews, key=lambda p: abs((_grams_from_pack(p.pack_size_label) or target_grams) - target_grams))


def _base_stmt():
    return (
        select(DBCanonicalSKU, ProviderSKUMapping)
        .join(ProviderSKUMapping, DBCanonicalSKU.id == ProviderSKUMapping.canonical_sku_id)
        .where(ProviderSKUMapping.provider == PROVIDER)
        .where(DBCanonicalSKU.active.is_(True))
        .order_by(
            desc(DBCanonicalSKU.brand_partnership_weight),
            ProviderSKUMapping.last_seen_at.desc().nullslast(),
            DBCanonicalSKU.display_name_en,
        )
    )


async def _previews_from_stmt(stmt, *, limit: int, in_stock_only: bool) -> list[SkuPreview]:
    async with get_session() as session:
        result = await session.execute(stmt.limit(max(limit * 4, limit)))
        rows = result.all()

    provider_ids = [mapping.provider_sku_id for _, mapping in rows]
    availability = {}
    if provider_ids:
        try:
            availability = await provider_router.grocery().check_availability(
                provider_ids,
                _default_location(),
            )
        except Exception:
            availability = {}

    previews: list[SkuPreview] = []
    for sku, mapping in rows:
        avail = availability.get(mapping.provider_sku_id)
        in_stock = bool(avail.available) if avail is not None else bool(mapping.available)
        if in_stock_only and not in_stock:
            continue
        price = (
            int(avail.current_price_inr)
            if avail is not None and avail.current_price_inr is not None
            else int(mapping.last_price_inr or sku.typical_price_band_min_inr or 0)
        )
        eta = avail.delivery_eta_min if avail is not None else None
        previews.append(
            SkuPreview(
                canonical_key=sku.canonical_key,
                display_name=sku.display_name_en,
                brand=sku.brand or "",
                pack_size_label=sku.pack_size or "",
                price_inr=price,
                in_stock=in_stock,
                provider_specific_id=mapping.provider_sku_id,
                category=sku.category,
                subcategory=sku.subcategory or "",
                unit=_unit_from_pack(sku.pack_size or ""),
                pack_quantity=_quantity_from_pack(sku.pack_size or ""),
                eta_min=eta,
            )
        )
        if len(previews) >= limit:
            break
    return previews


def _default_location() -> Location:
    return Location(latitude=17.4486, longitude=78.3792, pincode="500032", city="Hyderabad")


def _grams_from_pack(pack_size: str) -> int | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g)", pack_size.lower())
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    return int(value * 1000) if unit == "kg" else int(value)


def _unit_from_pack(pack_size: str) -> str:
    match = re.search(r"\d+(?:\.\d+)?\s*([a-zA-Z]+)", pack_size)
    return match.group(1) if match else "unit"


def _quantity_from_pack(pack_size: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)", pack_size)
    if not match:
        return 1.0
    return float(Decimal(match.group(1)))
