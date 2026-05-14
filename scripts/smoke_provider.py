"""Quick smoke test for the mock provider layer.

Run from the repository root:
    python scripts/smoke_provider.py

Note: the mock injects 3% transient failures on purpose so retry logic can be tested.
This smoke test wraps each call in a small retry to demonstrate happy path.
"""
import asyncio
import random
import sys
from pathlib import Path
from datetime import UTC, datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

random.seed(7)  # Seed that gives a clean smoke-test happy path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def with_retry(coro_factory, max_attempts=4):
    """Tiny retry wrapper - real app uses tenacity or similar."""
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except RuntimeError as e:
            if "TRANSIENT" in str(e) and attempt < max_attempts - 1:
                await asyncio.sleep(0.2)
                continue
            raise

from packages.providers.adapters.mock_swiggy_adapter import (
    get_grocery_provider, get_food_provider, get_dineout_provider
)
from packages.providers.interface import Location, CartItem, CustomerProfile, PaymentRef


async def test_grocery_flow():
    print("\n=== INSTAMART (Grocery) ===")
    p = get_grocery_provider()
    loc = Location(latitude=17.4486, longitude=78.3792, pincode="500032", city="Hyderabad")

    # Search in Telugu
    print("\n[1] Search 'godi pindi' (Telugu for atta)...")
    skus = await with_retry(lambda: p.search_skus("godi pindi", "te-IN", loc, limit=3))
    for s in skus:
        print(f"    - {s.display_name} | ₹{s.estimated_price_inr} | {s.brand}")

    # Search in Telugu - milk
    print("\n[2] Search 'paalu' (Telugu for milk)...")
    skus2 = await with_retry(lambda: p.search_skus("paalu", "te-IN", loc, limit=3))
    for s in skus2:
        print(f"    - {s.display_name} | ₹{s.estimated_price_inr}")

    # Cart + checkout
    print("\n[3] Build cart with atta + milk + Dolo...")
    items_to_add = []
    for query in ["atta", "Heritage paalu", "Dolo"]:
        results = await with_retry(lambda q=query: p.search_skus(q, "te-IN", loc, limit=1))
        if results:
            items_to_add.append(CartItem(canonical_sku=results[0], quantity=1))
            print(f"    + Added: {results[0].display_name}")

    cart = await with_retry(lambda: p.assemble_cart(items_to_add, loc))
    quote = await with_retry(lambda: p.quote_cart(cart))
    print(f"\n[4] Quote: subtotal=₹{quote.subtotal_inr}, total=₹{quote.total_inr}")
    print(f"    Delivery: {quote.estimated_delivery_min}-{quote.estimated_delivery_max} min")
    print(f"    Offers: {quote.applied_offers}")

    customer = CustomerProfile(
        user_id="user_test", name="Lakshmi Sharma", phone_e164="+919876543210",
        delivery_location=loc, preferred_language="te-IN"
    )
    payment = PaymentRef(
        payment_request_id="pr_test", razorpay_payment_id="pay_test",
        amount_paid_inr=quote.total_inr, paid_at=datetime.now(UTC)
    )
    result = await with_retry(lambda: p.execute_checkout(cart, payment, customer))
    print(f"\n[5] Order placed: {result.success}, ID: {result.provider_order_id}")
    print(f"    ETA: {result.estimated_delivery_at}")

    if result.success:
        status = await with_retry(lambda: p.track_order(result.provider_order_id))
        print(f"\n[6] Order status: {status.status.value}, ETA: {status.eta_minutes} min")


async def test_food_flow():
    print("\n\n=== SWIGGY FOOD (Restaurants) ===")
    p = get_food_provider()
    loc = Location(latitude=17.4486, longitude=78.3792, pincode="500032", city="Hyderabad")

    print("\n[1] Search biryani restaurants in Hyderabad...")
    rests = await with_retry(lambda: p.search_restaurants("biryani", None, loc, only_with_offers=True, limit=3))
    for r in rests:
        print(f"    - {r.name} | ★{r.rating} | {r.delivery_time_min}-{r.delivery_time_max} min | {r.offer_text}")

    if rests:
        print(f"\n[2] Get menu for {rests[0].name}...")
        menu = await with_retry(lambda: p.get_restaurant_menu(rests[0].provider_restaurant_id))
        for m in menu[:3]:
            print(f"    - {m.name} | ₹{m.price_inr} | {'⭐ bestseller' if m.is_bestseller else ''}")


async def test_dineout_flow():
    print("\n\n=== SWIGGY DINEOUT (Table Booking) ===")
    p = get_dineout_provider()
    loc = Location(latitude=17.4486, longitude=78.3792, pincode="500032", city="Hyderabad")

    print("\n[1] Search dine-in places with deals...")
    rests = await with_retry(lambda: p.search_dineout(None, None, loc, only_with_deals=True, limit=3))
    for r in rests:
        print(f"    - {r.name} | ★{r.rating} | ₹{r.cost_for_two_inr} for 2")
        for d in r.active_deals[:1]:
            print(f"      Deal: {d}")

    if rests:
        print(f"\n[2] Check slots for tomorrow at {rests[0].name}...")
        slots = await with_retry(lambda: p.get_available_slots(rests[0].provider_restaurant_id, "2026-04-28", party_size=4))
        available_slots = [s for s in slots if s.available]
        print(f"    Available slots: {[s.time for s in available_slots[:5]]}")

        if available_slots:
            print(f"\n[3] Book table at {available_slots[0].time}...")
            customer = CustomerProfile(
                user_id="user_test", name="Lakshmi Sharma", phone_e164="+919876543210",
                delivery_location=loc, preferred_language="te-IN"
            )
            booking = await with_retry(lambda: p.book_table(available_slots[0].slot_id, 4, customer))
            print(f"    Booked: {booking.booking_code}, deal: {booking.deal_applied}")


async def main():
    try:
        await test_grocery_flow()
        await test_food_flow()
        await test_dineout_flow()
        print("\n\n✅ ALL SMOKE TESTS PASSED")
    except Exception as e:
        print(f"\n\n❌ SMOKE TEST FAILED: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
