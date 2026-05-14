"""Seed script — populate the database with sample data for development.

Run from the repository root:
    python scripts/seed.py

Creates:
  - 1 demo family (Sharma Family, Hyderabad)
  - 2 users (Amma — ordering, Kiran — payer)
  - 1 family payer configuration
  - 10 canonical SKUs with embeddings (random 1024-dim vectors for testing)
  - Provider SKU mappings for all 10 SKUs
  - 50 Telugu vocabulary terms for common grocery items
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from sqlalchemy import text
from packages.core.db import engine, async_session_factory
from packages.core.models import (
    Base, Family, User, FamilyPayer, CanonicalSKU,
    ProviderSKUMapping, VocabularyTerm,
)


def _utcnow():
    return datetime.now(timezone.utc)


def _random_embedding(dim: int = 1024) -> list[float]:
    """Random unit-length embedding for testing pgvector search."""
    vec = np.random.randn(dim).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec.tolist()


# ============================================================================
# Sample Data
# ============================================================================

FAMILY_ID = uuid.UUID("aaaaaaaa-0001-0001-0001-000000000001")
AMMA_ID = uuid.UUID("bbbbbbbb-0001-0001-0001-000000000001")
KIRAN_ID = uuid.UUID("bbbbbbbb-0002-0002-0002-000000000002")

SKUS = [
    {
        "canonical_key": "aashirvaad_select_atta_5kg",
        "display_name_en": "Aashirvaad Select Atta 5kg",
        "display_names_local": {"te-IN": ["godi pindi", "atta", "గోధుమ పిండి"]},
        "category": "staples_flour",
        "subcategory": "wheat_flour",
        "brand": "aashirvaad",
        "pack_size": "5kg",
        "price_min": 280, "price_max": 340,
        "price": 310,
        "provider_id": "INST-HYD-FLOUR-001",
    },
    {
        "canonical_key": "heritage_toned_milk_500ml",
        "display_name_en": "Heritage Toned Milk 500ml",
        "display_names_local": {"te-IN": ["paalu", "palu", "పాలు", "milk"]},
        "category": "dairy_milk",
        "subcategory": "toned_milk",
        "brand": "heritage",
        "pack_size": "500ml",
        "price_min": 25, "price_max": 30,
        "price": 27,
        "provider_id": "INST-HYD-MILK-001",
    },
    {
        "canonical_key": "dolo_650_15tab",
        "display_name_en": "Dolo 650 (15 Tablets)",
        "display_names_local": {"te-IN": ["dolo", "dolo tablet", "జ్వర మాత్ర"]},
        "category": "otc_medicine",
        "subcategory": "paracetamol",
        "brand": "dolo",
        "pack_size": "15 tablets",
        "price_min": 30, "price_max": 40,
        "price": 35,
        "provider_id": "INST-HYD-MED-001",
    },
    {
        "canonical_key": "mtr_rava_idli_500g",
        "display_name_en": "MTR Rava Idli Mix 500g",
        "display_names_local": {"te-IN": ["rava", "ravva", "రవ్వ", "sooji"]},
        "category": "instant_mix",
        "subcategory": "idli_mix",
        "brand": "mtr",
        "pack_size": "500g",
        "price_min": 55, "price_max": 70,
        "price": 62,
        "provider_id": "INST-HYD-MIX-001",
    },
    {
        "canonical_key": "priya_red_chilli_powder_200g",
        "display_name_en": "Priya Red Chilli Powder 200g",
        "display_names_local": {"te-IN": ["karam", "kaaram", "Priya kaaram", "chilli powder"]},
        "category": "spices_powder",
        "subcategory": "chilli_powder",
        "brand": "priya",
        "pack_size": "200g",
        "price_min": 80, "price_max": 100,
        "price": 88,
        "provider_id": "INST-HYD-SPICE-001",
    },
    {
        "canonical_key": "redlabel_tea_500g",
        "display_name_en": "Brooke Bond Red Label Tea 500g",
        "display_names_local": {"te-IN": ["tea powder", "Red Label", "chai"], "hi-IN": ["chai patti", "Red Label"]},
        "category": "beverages_tea",
        "subcategory": "black_tea",
        "brand": "red_label",
        "pack_size": "500g",
        "price_min": 275, "price_max": 320,
        "price": 295,
        "provider_id": "INST-HYD-TEA-001",
    },
    {
        "canonical_key": "india_gate_basmati_5kg",
        "display_name_en": "India Gate Basmati Rice 5kg",
        "display_names_local": {"te-IN": ["biyyam", "rice", "బియ్యం", "basmati"]},
        "category": "staples_rice",
        "subcategory": "basmati_rice",
        "brand": "india_gate",
        "pack_size": "5kg",
        "price_min": 450, "price_max": 550,
        "price": 499,
        "provider_id": "INST-HYD-RICE-001",
    },
    {
        "canonical_key": "amul_butter_500g",
        "display_name_en": "Amul Butter 500g",
        "display_names_local": {"te-IN": ["venna", "butter", "వెన్న"]},
        "category": "dairy_butter",
        "subcategory": "salted_butter",
        "brand": "amul",
        "pack_size": "500g",
        "price_min": 260, "price_max": 290,
        "price": 275,
        "provider_id": "INST-HYD-BUTTER-001",
    },
    {
        "canonical_key": "fortune_sunflower_oil_1l",
        "display_name_en": "Fortune Sunflower Oil 1L",
        "display_names_local": {"te-IN": ["nune", "oil", "నూనె", "sunflower oil"]},
        "category": "staples_oil",
        "subcategory": "sunflower_oil",
        "brand": "fortune",
        "pack_size": "1L",
        "price_min": 140, "price_max": 170,
        "price": 155,
        "provider_id": "INST-HYD-OIL-001",
    },
    {
        "canonical_key": "heritage_curd_500g",
        "display_name_en": "Heritage Curd Cup 500g",
        "display_names_local": {"te-IN": ["perugu", "Heritage perugu", "curd"], "hi-IN": ["dahi"]},
        "category": "dairy_curd",
        "subcategory": "set_curd",
        "brand": "heritage",
        "pack_size": "500g",
        "price_min": 42, "price_max": 50,
        "price": 45,
        "provider_id": "INST-HYD-CURD-001",
    },
]

VOCAB_TERMS = [
    # Atta / Flour
    ("godi pindi", "staples_flour", None, "Telugu for wheat flour"),
    ("atta", "staples_flour", None, "Hindi/English for wheat flour"),
    ("గోధుమ పిండి", "staples_flour", None, "Telugu script — wheat flour"),
    ("godhuma pindi", "staples_flour", None, "Romanized Telugu — wheat flour"),
    ("maida", "staples_flour", None, "Refined flour / all purpose"),
    # Milk
    ("paalu", "dairy_milk", None, "Telugu for milk"),
    ("palu", "dairy_milk", None, "Telugu variant for milk"),
    ("పాలు", "dairy_milk", None, "Telugu script — milk"),
    ("milk", "dairy_milk", None, "English — milk"),
    ("duddu", "dairy_milk", None, "Informal Telugu — milk"),
    # Rice
    ("biyyam", "staples_rice", None, "Telugu for rice"),
    ("బియ్యం", "staples_rice", None, "Telugu script — rice"),
    ("rice", "staples_rice", None, "English — rice"),
    ("basmati", "staples_rice", None, "Basmati rice"),
    ("sona masoori", "staples_rice", None, "South Indian rice variety"),
    # Curd / Yogurt
    ("perugu", "dairy_curd", None, "Telugu for curd — Telangana"),
    ("పెరుగు", "dairy_curd", None, "Telugu script — curd"),
    ("dahi", "dairy_curd", None, "Hindi for curd"),
    ("curd", "dairy_curd", None, "English — curd"),
    # Buttermilk (regional ambiguity)
    ("majjiga", "dairy_buttermilk", "telangana", "Buttermilk — Telangana dialect"),
    ("chaas", "dairy_buttermilk", None, "Hindi for buttermilk"),
    # Oil
    ("nune", "staples_oil", None, "Telugu for oil"),
    ("నూనె", "staples_oil", None, "Telugu script — oil"),
    ("oil", "staples_oil", None, "English — oil"),
    ("nuvvula nune", "staples_oil", None, "Sesame oil — Telugu"),
    # Tea
    ("tee podi", "beverages_tea", None, "Telugu for tea powder"),
    ("tea", "beverages_tea", None, "English — tea"),
    ("టీ పొడి", "beverages_tea", None, "Telugu script — tea powder"),
    ("chai", "beverages_tea", None, "Hindi — tea"),
    # Sugar
    ("panchidara", "staples_sugar", None, "Telugu for sugar"),
    ("sugar", "staples_sugar", None, "English — sugar"),
    ("చక్కెర", "staples_sugar", None, "Telugu script — sugar (chakkera)"),
    ("bellam", "staples_jaggery", None, "Telugu for jaggery"),
    # Pickle
    ("avakaya", "condiments_pickle", None, "Telugu for mango pickle"),
    ("avakai", "condiments_pickle", None, "Telugu variant — mango pickle"),
    ("ఆవకాయ", "condiments_pickle", None, "Telugu script — mango pickle"),
    ("pickle", "condiments_pickle", None, "English — pickle"),
    # Butter
    ("venna", "dairy_butter", None, "Telugu for butter"),
    ("butter", "dairy_butter", None, "English — butter"),
    ("వెన్న", "dairy_butter", None, "Telugu script — butter"),
    # Medicine
    ("dolo", "medicine_otc", "dolo", "OTC paracetamol brand"),
    ("tablet", "medicine_otc", None, "Generic tablet reference"),
    ("crocin", "medicine_otc", "crocin", "OTC paracetamol brand — generic usage"),
    # Rava / Sooji
    ("rava", "staples_flour", None, "Telugu/Hindi for semolina"),
    ("ravva", "staples_flour", None, "Telugu variant — semolina"),
    ("sooji", "staples_flour", None, "Hindi for semolina"),
    ("రవ్వ", "staples_flour", None, "Telugu script — semolina"),
    # Vegetables (generic)
    ("kooragayalu", "vegetables", None, "Telugu for vegetables"),
    ("vegetables", "vegetables", None, "English — vegetables"),
    ("ulli", "vegetables_onion", None, "Telugu for onion"),
    ("tomato", "vegetables_tomato", None, "Tomato — same in Telugu"),
]


async def seed():
    print("🌱 Seeding foodleaf database...\n")

    async with async_session_factory() as session:
        # Check if already seeded
        result = await session.execute(
            text("SELECT COUNT(*) FROM families WHERE id = :id"),
            {"id": str(FAMILY_ID)},
        )
        if result.scalar() > 0:
            print("⚠️  Database already seeded. Drop tables or truncate to re-seed.")
            return

        # 1. Family
        family = Family(
            id=FAMILY_ID,
            display_name="Sharma Family",
            primary_locale="te-IN",
            city="Hyderabad",
            approval_threshold_inr=1500,
            care_features_enabled=False,
        )
        session.add(family)
        await session.flush()
        print("✅ Created family: Sharma Family")

        # 2. Users
        amma = User(
            id=AMMA_ID,
            family_id=FAMILY_ID,
            role="ordering_user",
            relationship_label="parent",
            display_name="Amma",
            phone_e164="+919876543210",
            whatsapp_phone_e164="+919876543210",
            preferred_language="te-IN",
            dietary_constraints={"vegetarian": False, "diabetic": False},
            brand_preferences={"milk": "heritage", "atta": "aashirvaad"},
        )
        kiran = User(
            id=KIRAN_ID,
            family_id=FAMILY_ID,
            role="both",
            relationship_label="child",
            display_name="Kiran",
            phone_e164="+919876543211",
            whatsapp_phone_e164="+919876543211",
            preferred_language="te-IN",
        )
        session.add_all([amma, kiran])
        await session.flush()

        # Set default payer
        family.default_payer_user_id = KIRAN_ID
        print("✅ Created users: Amma (ordering), Kiran (payer)")

        # 3. Family Payer
        payer = FamilyPayer(
            family_id=FAMILY_ID,
            user_id=KIRAN_ID,
            upi_handle="kiran@okaxis",
            is_default_payer=True,
            category_routing={"groceries": str(KIRAN_ID), "food": str(KIRAN_ID)},
            auto_approve_threshold_inr=500,
            trust_started_at=_utcnow(),
        )
        session.add(payer)
        print("✅ Created family payer: Kiran (kiran@okaxis)")

        # 4. Canonical SKUs with random embeddings
        np.random.seed(42)  # Reproducible embeddings
        for sku_data in SKUS:
            sku = CanonicalSKU(
                canonical_key=sku_data["canonical_key"],
                display_name_en=sku_data["display_name_en"],
                display_names_local=sku_data["display_names_local"],
                category=sku_data["category"],
                subcategory=sku_data["subcategory"],
                brand=sku_data["brand"],
                pack_size=sku_data["pack_size"],
                typical_price_band_min_inr=sku_data["price_min"],
                typical_price_band_max_inr=sku_data["price_max"],
                embedding=_random_embedding(),
                brand_partnership_weight=0.0,
                last_seen_at=_utcnow(),
            )
            session.add(sku)
            await session.flush()

            # Provider mapping
            mapping = ProviderSKUMapping(
                canonical_sku_id=sku.id,
                provider="swiggy_instamart_mcp",
                provider_sku_id=sku_data["provider_id"],
                city="Hyderabad",
                available=True,
                last_price_inr=sku_data["price"],
                last_seen_at=_utcnow(),
            )
            session.add(mapping)

        print(f"✅ Created {len(SKUS)} canonical SKUs with embeddings + provider mappings")

        # 5. Vocabulary terms
        for term_text, category, brand, notes in VOCAB_TERMS:
            term = VocabularyTerm(
                term=term_text,
                language="te-IN",
                maps_to_category=category,
                maps_to_brand=brand,
                notes=notes,
                confidence=1.0,
            )
            session.add(term)
        print(f"✅ Created {len(VOCAB_TERMS)} vocabulary terms")

        await session.commit()

    # 6. Verify pgvector works
    print("\n[VERIFY] pgvector cosine similarity search...")
    async with engine.connect() as conn:
        query_vec = _random_embedding()
        vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_vec) + "]"
        # Use string interpolation for the vector literal since asyncpg
        # doesn't support ::vector cast with bind params
        raw = await conn.execute(
            text(
                "SELECT canonical_key, display_name_en, "
                f"1 - (embedding <=> '{vec_literal}'::vector) AS similarity "
                "FROM canonical_skus "
                f"ORDER BY embedding <=> '{vec_literal}'::vector "
                "LIMIT 3"
            )
        )
        rows = raw.fetchall()
        print("   Top 3 similar SKUs (random query vector):")
        for key, name, sim in rows:
            print(f"   - {name} (similarity: {float(sim):.4f})")

    print("\n[OK] Seed complete! Database ready for development.")


if __name__ == "__main__":
    asyncio.run(seed())
