import logging
import uuid
from datetime import datetime, timezone

from packages.core.db import get_session
from packages.core.models import Order, User
from packages.providers.interface import (
    CartHandle, CustomerProfile, Location, PaymentRef, ProviderName, CartItem, CanonicalSKU
)
from packages.providers.adapters.mock_swiggy_adapter import MockSwiggyInstamartAdapter

logger = logging.getLogger("foodleaf.executor")

async def execute_order(user_id: str, family_id: str, cart_data: dict, voice_session_id: str | None = None) -> Order | None:
    """Execute checkout via the provider and persist the Order in the DB."""
    
    # 1. Look up user details for CustomerProfile
    async with get_session() as session:
        user = await session.get(User, uuid.UUID(user_id))
        if not user:
            logger.error(f"User {user_id} not found for execution.")
            return None
            
    customer = CustomerProfile(
        user_id=user_id,
        name=user.display_name or "User",
        phone_e164=user.whatsapp_phone_e164,
        delivery_location=Location(latitude=17.4486, longitude=78.3792, pincode="500032", city="Hyderabad"),
        preferred_language=user.preferred_language or "te-IN"
    )

    if cart_data.get("flow") == "discovery":
        return await _persist_discovery_order(user_id, family_id, cart_data, voice_session_id)

    # 2. Rehydrate CartHandle
    cart_items = [
        CartItem(
            canonical_sku=CanonicalSKU(
                canonical_key=i["canonical_key"],
                display_name=i["display_name"],
                display_names_local={},
                category="",
                subcategory="",
                brand=i.get("brand", ""),
                pack_size="",
                unit="",
                pack_quantity=1,
                estimated_price_inr=i.get("price_inr", 0),
                typical_price_band_min_inr=0,
                typical_price_band_max_inr=0,
                image_url=None,
                provider_specific_id="",
                provider=ProviderName.SWIGGY_INSTAMART,
                in_stock=True,
                delivery_eta_min=None
            ),
            quantity=i["quantity"]
        )
        for i in cart_data.get("items", [])
    ]
    
    provider_name = ProviderName(cart_data.get("provider", "swiggy_instamart"))
    
    cart = CartHandle(
        provider=provider_name,
        provider_cart_id=cart_data.get("provider_cart_id"),
        items=cart_items,
        expires_at=None
    )
    
    # 3. Create dummy PaymentRef (assuming prepaid for MVP)
    payment = PaymentRef(
        payment_request_id="dummy_req",
        razorpay_payment_id="dummy_rp",
        amount_paid_inr=cart_data.get("quote_total_inr", 0),
        paid_at=datetime.now(timezone.utc)
    )
    
    # 4. Call provider
    provider = MockSwiggyInstamartAdapter()
    result = await provider.execute_checkout(cart, payment, customer)
    
    if not result.success:
        logger.error(f"Checkout failed: {result.failure_reason}")
        return None
        
    # 5. Persist Order in DB
    order = Order(
        family_id=uuid.UUID(family_id),
        ordering_user_id=uuid.UUID(user_id),
        voice_session_id=uuid.UUID(voice_session_id) if voice_session_id and len(voice_session_id) == 36 else None,
        provider=provider_name.value,
        provider_order_id=result.provider_order_id,
        cart_items=cart_data,
        total_inr=cart_data.get("quote_total_inr", 0),
        status="confirmed",
        placed_at=datetime.now(timezone.utc),
    )
    try:
        async with get_session() as session:
            session.add(order)
    except Exception:
        logger.warning("Order persist failed with voice_session_id FK, retrying without it")
        order.voice_session_id = None
        try:
            async with get_session() as session:
                session.add(order)
        except Exception as e2:
            logger.error("Order persist failed entirely (non-fatal): %s", e2)
        
    logger.info(f"Order persisted. Provider ID: {result.provider_order_id}")
    return order


async def _persist_discovery_order(
    user_id: str,
    family_id: str,
    cart_data: dict,
    voice_session_id: str | None = None,
) -> Order:
    """Persist a selected discovery option as a mock confirmed order/booking."""
    source = cart_data.get("source", "food")
    prefix = {
        "food": "FOOD-ORD",
        "instamart": "INST-ORD",
        "dineout": "DINE-BOOK",
    }.get(source, "DISC-ORD")

    order = Order(
        family_id=uuid.UUID(family_id),
        ordering_user_id=uuid.UUID(user_id),
        voice_session_id=uuid.UUID(voice_session_id) if voice_session_id and len(voice_session_id) == 36 else None,
        provider=cart_data.get("provider"),
        provider_order_id=f"{prefix}-{uuid.uuid4().hex[:10].upper()}",
        cart_items=cart_data,
        total_inr=cart_data.get("quote_total_inr", 0),
        status="confirmed",
        placed_at=datetime.now(timezone.utc),
    )
    try:
        async with get_session() as session:
            session.add(order)
    except Exception:
        order.voice_session_id = None
        try:
            async with get_session() as session:
                session.add(order)
        except Exception as e2:
            logger.error("Discovery order persist failed (non-fatal): %s", e2)

    logger.info("Discovery order persisted. Provider ID: %s", order.provider_order_id)
    return order
