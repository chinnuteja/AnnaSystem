"""Seed the Sharma family demo data for Anna MVP.

Creates:
  - Sharma family (primary_locale=hi-IN, city=Delhi, approval_threshold=1500)
  - Maa (Sunita Sharma): care_recipient, Hindi-speaking, WhatsApp phone
  - Beta (Rahul Sharma): payer, English/Hinglish, WhatsApp phone
  - FamilyPayer record linking Rahul as default payer

Run:  python -m scripts.seed_anna_demo
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from packages.core.db import get_session, init_db
from packages.core.models import Family, FamilyPayer, User


# Demo phone numbers (E.164 format, use test prefixes)
MAA_PHONE = "+919876500001"
MAA_WHATSAPP = "+919876500001"
BETA_PHONE = "+919876500002"
BETA_WHATSAPP = "+919876500002"

FAMILY_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")
MAA_ID = uuid.UUID("a0000000-0000-0000-0000-000000000010")
BETA_ID = uuid.UUID("a0000000-0000-0000-0000-000000000020")
PAYER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000030")


async def seed() -> None:
    await init_db()

    async with get_session() as session:
        # Check if already seeded
        existing = await session.execute(
            select(Family).where(Family.id == FAMILY_ID)
        )
        if existing.scalars().first():
            print("⚠️  Sharma family already exists, skipping seed.")
            return

        # 1. Create family
        family = Family(
            id=FAMILY_ID,
            display_name="Sharma Family",
            default_payer_user_id=BETA_ID,
            primary_locale="hi-IN",
            city="Delhi",
            approval_threshold_inr=1500,
            care_features_enabled=True,
        )
        session.add(family)

        # 2. Create Maa (Sunita)
        maa = User(
            id=MAA_ID,
            family_id=FAMILY_ID,
            role="ordering_user",
            relationship_label="Maa",
            display_name="Sunita Sharma",
            phone_e164=MAA_PHONE,
            whatsapp_phone_e164=MAA_WHATSAPP,
            preferred_language="hi-IN",
            dietary_constraints={"vegetarian": True, "no_onion_garlic": False},
            brand_preferences={"atta": "Aashirvaad", "oil": "Fortune"},
            active=True,
        )
        session.add(maa)

        # 3. Create Beta (Rahul)
        beta = User(
            id=BETA_ID,
            family_id=FAMILY_ID,
            role="payer",
            relationship_label="Beta",
            display_name="Rahul Sharma",
            phone_e164=BETA_PHONE,
            whatsapp_phone_e164=BETA_WHATSAPP,
            preferred_language="en-IN",
            dietary_constraints=None,
            brand_preferences=None,
            active=True,
        )
        session.add(beta)

        # 4. Create FamilyPayer record
        payer = FamilyPayer(
            id=PAYER_ID,
            family_id=FAMILY_ID,
            user_id=BETA_ID,
            upi_handle="rahul@paytm",
            is_default_payer=True,
            auto_approve_threshold_inr=500,
            active=True,
        )
        session.add(payer)

        await session.commit()

    print("✅ Sharma family seeded successfully!")
    print(f"   Family:  {FAMILY_ID} (Sharma Family, Delhi, hi-IN)")
    print(f"   Maa:     {MAA_ID}  Sunita Sharma  📞 {MAA_PHONE}  (ordering_user, hi-IN)")
    print(f"   Beta:    {BETA_ID}  Rahul Sharma   📞 {BETA_PHONE}  (payer, en-IN)")
    print(f"   Payer:   {PAYER_ID}  Rahul → upi:rahul@paytm  auto-approve ≤ ₹500")
    print(f"   Threshold: ₹1500")


if __name__ == "__main__":
    asyncio.run(seed())
