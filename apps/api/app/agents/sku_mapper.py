import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import json

from openai import AsyncAzureOpenAI
from sqlalchemy import String, desc, select, or_, and_, cast
from sqlalchemy.dialects.postgresql import JSONB

from app.schemas.message import CandidateItem, ParsedIntent
from packages.providers.interface import CartItem, Location, CanonicalSKU as InterfaceCanonicalSKU, ProviderName
from packages.providers.catalog_helpers import find_substitutes
from packages.providers.router import provider_router
from packages.core.db import get_session
from packages.core.models import CanonicalSKU as DB_CanonicalSKU, ProviderSKUMapping, VocabularyTerm
from app.core.config import settings

logger = logging.getLogger("foodleaf.sku_mapper")

_openai_client = AsyncAzureOpenAI(
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
    api_version=settings.azure_openai_api_version,
)
T = TypeVar("T")


@dataclass(frozen=True)
class SemanticCategoryHint:
    category: str
    keyword: str | None = None


# Known queries where semantic fallback consistently gives false positives.
# These skip LLM fallback and go straight to "not found".
_SEMANTIC_BLOCKLIST: set[str] = {
    "peanut butter",
    "almond milk",
    "quinoa",
    "tahini",
    "pesto",
    "soy sauce",
    "tofu",
    "hummus",
    "chia seeds",
}

_CATEGORY_CACHE: dict[str, object] = {"ts": 0.0, "categories": []}
_CATEGORY_CACHE_TTL_SEC = 10 * 60


async def resolve_and_quote(intent: ParsedIntent, location: Location):
    if intent.action != "ORDER" or not intent.items:
        return [], None, None

    provider = provider_router.grocery()
    cart_items: list[CartItem] = []
    candidates: list[CandidateItem] = []

    async with get_session() as session:
        for item in intent.items:
            # 1. Look up vocabulary for regional term (e.g., "godi pindi" -> "staples_flour")
            query_text = item.text.lower().strip()
            search_text = _search_text_for_item(query_text)
            
            vocab_result = await session.execute(
                select(VocabularyTerm).where(VocabularyTerm.term.ilike(f"%{search_text}%"))
            )
            vocab_term = vocab_result.scalars().first()
            
            # 2. Build CanonicalSKU query
            stmt = select(DB_CanonicalSKU, ProviderSKUMapping).join(
                ProviderSKUMapping, DB_CanonicalSKU.id == ProviderSKUMapping.canonical_sku_id
            ).where(
                ProviderSKUMapping.provider == "swiggy_instamart_mcp"
            )

            if vocab_term:
                logger.info(f"Vocab match for '{search_text}': category={vocab_term.maps_to_category}")
                conditions = [DB_CanonicalSKU.category == vocab_term.maps_to_category]
                if vocab_term.maps_to_brand:
                    conditions.append(DB_CanonicalSKU.brand == vocab_term.maps_to_brand)
                stmt = stmt.where(and_(*conditions))
            else:
                # 3. Fallback to text match if no vocabulary (since embeddings are random right now)
                logger.info(f"No vocab match for '{search_text}', falling back to text search")
                stmt = stmt.where(
                    or_(
                        DB_CanonicalSKU.display_name_en.ilike(f"%{search_text}%"),
                        DB_CanonicalSKU.brand.ilike(f"%{search_text}%"),
                        # A bit hacky: cast JSONB to text to search inside it for simplicity
                        cast(DB_CanonicalSKU.display_names_local, String).ilike(f"%{search_text}%")
                    )
                )

            # Get top 1 result (ordered so broad matches are stable)
            stmt = stmt.order_by(
                desc(DB_CanonicalSKU.brand_partnership_weight),
                DB_CanonicalSKU.last_seen_at.desc().nullslast(),
                DB_CanonicalSKU.display_name_en,
            ).limit(1)
            result = await session.execute(stmt)
            row = result.first()
            
            if not row and not vocab_term:
                logger.info(f"Text search failed for '{search_text}'. Attempting semantic fallback.")
                if search_text in _SEMANTIC_BLOCKLIST:
                    logger.info("Query '%s' is in semantic blocklist — skipping LLM fallback", search_text)
                    semantic_hint = None
                else:
                    semantic_hint = await _semantic_sku_search(search_text, session)
                if semantic_hint and semantic_hint.category:
                    logger.info(
                        "Semantic fallback mapped '%s' to category '%s' (keyword=%r)",
                        search_text,
                        semantic_hint.category,
                        semantic_hint.keyword,
                    )
                    stmt = select(DB_CanonicalSKU, ProviderSKUMapping).join(
                        ProviderSKUMapping, DB_CanonicalSKU.id == ProviderSKUMapping.canonical_sku_id
                    ).where(
                        ProviderSKUMapping.provider == "swiggy_instamart_mcp",
                        DB_CanonicalSKU.category == semantic_hint.category,
                    )
                    if semantic_hint.keyword:
                        kw = semantic_hint.keyword.strip().lower()
                        if kw:
                            stmt = stmt.where(
                                or_(
                                    DB_CanonicalSKU.display_name_en.ilike(f"%{kw}%"),
                                    DB_CanonicalSKU.brand.ilike(f"%{kw}%"),
                                    cast(DB_CanonicalSKU.display_names_local, String).ilike(f"%{kw}%"),
                                )
                            )
                    stmt = stmt.order_by(
                        desc(DB_CanonicalSKU.brand_partnership_weight),
                        DB_CanonicalSKU.last_seen_at.desc().nullslast(),
                        DB_CanonicalSKU.display_name_en,
                    ).limit(1)
                    result = await session.execute(stmt)
                    row = result.first()

            if row:
                db_sku, mapping = row
                avail_res = await _with_provider_retry(
                    lambda: provider.check_availability([mapping.provider_sku_id], location)
                )
                avail = avail_res.get(mapping.provider_sku_id)
                in_stock = avail.available if avail else False
                current_price = avail.current_price_inr if avail else mapping.last_price_inr
                interface_sku = InterfaceCanonicalSKU(
                    canonical_key=db_sku.canonical_key,
                    display_name=db_sku.display_name_en,
                    display_names_local=db_sku.display_names_local or {},
                    category=db_sku.category,
                    subcategory=db_sku.subcategory or "",
                    brand=db_sku.brand or "",
                    pack_size=db_sku.pack_size or "1 item",
                    unit="unit", # Hardcoded for now
                    pack_quantity=1.0,
                    estimated_price_inr=current_price,
                    typical_price_band_min_inr=db_sku.typical_price_band_min_inr or current_price,
                    typical_price_band_max_inr=db_sku.typical_price_band_max_inr or current_price,
                    image_url=None,
                    provider_specific_id=mapping.provider_sku_id,
                    provider=ProviderName.SWIGGY_INSTAMART,
                    in_stock=in_stock,
                    delivery_eta_min=avail.delivery_eta_min if avail else 15,
                )
            else:
                logger.warning(f"No DB SKU matched for '{search_text}', falling back to mock provider")
                provider_skus = await _with_provider_retry(
                    lambda: provider.search_skus(search_text, "te-IN", location, limit=1)
                )
                if not provider_skus:
                    logger.warning(f"No provider SKU matched for '{search_text}'")
                    continue
                interface_sku = provider_skus[0]
                in_stock = interface_sku.in_stock

            quantity = item.quantity or 1
            requested_size_label = _extract_requested_size_label(item.text)
            notes = None
            if requested_size_label and requested_size_label.lower() != interface_sku.pack_size.lower():
                notes = requested_size_label

            substitutes = []
            if not in_stock:
                substitutes = await find_substitutes(
                    original_sku_id=interface_sku.canonical_key,
                    category=interface_sku.category,
                    limit=2,
                    in_stock_only=True,
                )

            cart_items.append(
                CartItem(
                    canonical_sku=interface_sku,
                    quantity=quantity,
                    notes=notes,
                    substitutes=substitutes,
                )
            )
            candidates.append(
                CandidateItem(
                    canonical_key=interface_sku.canonical_key,
                    display_name=interface_sku.display_name,
                    brand=interface_sku.brand,
                    price_inr=interface_sku.estimated_price_inr,
                    provider_specific_id=interface_sku.provider_specific_id,
                    in_stock=interface_sku.in_stock,
                )
            )

    if not cart_items:
        return candidates, None, None

    cart = await _with_provider_retry(lambda: provider.assemble_cart(cart_items, location))
    quote = await _with_provider_retry(lambda: provider.quote_cart(cart))
    return candidates, cart, quote


async def _with_provider_retry(fn: Callable[[], Awaitable[T]], attempts: int = 3) -> T:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as e:
            last_error = e
            logger.warning("Provider call failed on attempt %s/%s: %s", attempt + 1, attempts, e)
    assert last_error is not None
    raise last_error


_SIZE_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|gram|grams|l|ml|litre|liter|pack|packet|pc|pcs)")


def _extract_requested_size_label(text: str | None) -> str | None:
    if not text:
        return None
    m = _SIZE_RE.search(text.lower())
    if not m:
        return None
    num = m.group("num")
    unit = m.group("unit")
    normalize = {
        "gram": "g",
        "grams": "g",
        "liter": "L",
        "litre": "L",
        "l": "L",
        "ml": "ml",
        "kg": "kg",
        "g": "g",
        "pack": "pack",
        "packet": "packet",
        "pc": "pc",
        "pcs": "pcs",
    }
    unit_norm = normalize.get(unit, unit)
    return f"{num}{unit_norm}" if unit_norm in {"kg", "g", "L", "ml"} else f"{num} {unit_norm}"


def _search_text_for_item(text: str) -> str:
    cleaned = _SIZE_RE.sub(" ", text.lower())
    cleaned = re.sub(r"\b(and|with|plus|kavali|please|order|buy|get|naku|naaku|haa|ha|i|want|to)\b", " ", cleaned)
    return " ".join(cleaned.split()) or text.strip().lower()


async def _semantic_sku_search(query_text: str, session) -> SemanticCategoryHint | None:
    """Use LLM to map a query to a canonical category + keyword hint.

    Keyword helps avoid picking an arbitrary SKU inside a broad category.
    """
    categories = await _get_cached_categories(session)
    if not categories:
        return None

    prompt = (
        "You are a grocery catalog assistant for an INDIAN grocery delivery app.\n"
        "Given a user's query, map it to the most specific category from the list.\n"
        "Also provide a short keyword (1-3 words) to filter SKUs inside that category.\n\n"
        "CRITICAL RULES:\n"
        "1. If the query describes a product NOT in typical Indian groceries "
        "(e.g., peanut butter, almond milk, quinoa, pesto, tahini), return category \"unknown\".\n"
        "2. For compound names where a modifier changes the product type "
        "(e.g., 'peanut butter' is a nut spread, NOT dairy butter; "
        "'coconut oil' is different from 'sunflower oil'), return \"unknown\" unless the EXACT type exists.\n"
        "3. Only map to a category if the FULL product meaning clearly fits.\n"
        "4. The keyword must be the most specific descriptor from the query.\n\n"
        f"User query: {query_text!r}\n"
        f"Categories: {', '.join(categories)}\n\n"
        "Reply ONLY in strict JSON with keys: category, keyword.\n"
        "Example: {\"category\":\"dairy_curd\",\"keyword\":\"curd\"}\n"
    )

    try:
        response = await _openai_client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=80,
        )
        raw = (response.choices[0].message.content or "").strip()
        data = json.loads(raw)
        cat = str(data.get("category") or "").strip()
        if cat not in categories:
            return None
        kw = data.get("keyword")
        kw = str(kw).strip() if isinstance(kw, str) and kw.strip() else None
        return SemanticCategoryHint(category=cat, keyword=kw)
    except Exception as e:
        logger.error("Semantic SKU search failed: %s", e)
        return None


async def _get_cached_categories(session) -> list[str]:
    now = time.time()
    ts = float(_CATEGORY_CACHE.get("ts") or 0.0)
    cached = _CATEGORY_CACHE.get("categories") or []
    if cached and (now - ts) < _CATEGORY_CACHE_TTL_SEC:
        return list(cached)

    cats_result = await session.execute(select(DB_CanonicalSKU.category).distinct())
    categories = sorted({row[0] for row in cats_result if row and row[0]})
    _CATEGORY_CACHE["ts"] = now
    _CATEGORY_CACHE["categories"] = categories
    return categories
