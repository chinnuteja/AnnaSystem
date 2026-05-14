# FoodLeaf — Build Plan to Reach CONVERSATION_FLOW.md

> **Goal:** Bring the bot from "stateless-feeling, single-line replies" to the full 12-case + 12-edge-case dialogue contract in [CONVERSATION_FLOW.md](CONVERSATION_FLOW.md). Without hardcoding any catalog data, SKU names, brands, prices, sizes, or examples in Python strings.

---

## Why this is currently failing

The audit found three structural reasons the bot reads as stateless and incomplete:

1. **Reply generation is a 60-line `if/elif` ladder.** [`apps/api/app/agents/confirmation.py`](apps/api/app/agents/confirmation.py) maps intent → one fixed string. Cart confirmation today is literally `"Sare, {item_text} cart lo pettanu. Total ₹{quote.total_inr}..."` ([confirmation.py:58-61](apps/api/app/agents/confirmation.py#L58-L61)). The spec wants a multi-line itemized cart with subtotal, delivery fee, total, ETA, and address — none of which a single f-string can produce. **Verdict: the reply layer needs to become a renderer, not a switch.**

2. **Catalog signals exist but are ignored.** `instamart_catalog.json` has `in_stock`, `pack_size`, `pack_quantity`, `delivery_eta_min`, language-specific names. The SKU mapper reads `in_stock` ([sku_mapper.py:77](apps/api/app/agents/sku_mapper.py#L77)) but the confirmation never branches on it. Pack-size ("100g paneer" → "200g paneer") is silently substituted with no flag to the user. ETA from the catalog is overridden by hardcoded 15-25 min in the mock adapter ([mock_swiggy_adapter.py:212](packages/providers/adapters/mock_swiggy_adapter.py#L212)). **Verdict: the catalog is rich; we just don't read it where it matters.**

3. **Whole flows don't exist.** No address persistence. No real payment trigger (executor passes a `dummy_req` PaymentRef). No tracking backend (TRACK intent returns "Mee recent order status check chesthanu" — a stub, not a query). No mamulu/repeat detection. No anomaly check. No service-area gate. No voice-burst debouncing. No language matching. **Verdict: 12 of 24 spec cases need new code paths, not edits.**

---

## Architecture principles — apply to every change below

These are the rules that keep the system from drifting back into hardcoded strings:

- **The renderer never imports JSON.** Renderer functions take already-resolved structured data (a `Cart`, a `DiscoveryResult`, an `Order`) and produce text. Catalog lookup happens in upstream agents.
- **No hardcoded SKU names, brand names, sizes, prices, or example items in any Python string.** When a response says "Bahusha 'Apple Shimla' (1kg, ₹195)...", that string is built from a catalog query result, not typed into a template. Even welcome-message capability bullets ("Groceries, Food, Dine-in") may stay literal because they ARE the product offering — but their *examples* (atta, paalu, biryani) come from catalog samplers.
- **Templates carry structure, data carries content.** A template says "🛒 Cart:\n{items}\nSubtotal: ₹{subtotal}\nDelivery: ₹{delivery_fee}\nTotal: ₹{total}". The `{items}` is itself the result of `format_cart_lines(cart)` — also catalog-driven.
- **Language is a parameter, not a fork.** Every renderer accepts `language: str` (e.g. "te-IN", "en-IN", "hi-IN"). Two language packs to start (te-IN, en-IN); add more by dropping a file.
- **Sub-flows live in `context.flow`, not in new top-level states.** `AWAITING_CONFIRMATION` already hosts `awaiting_assistant`, `awaiting_location`, `discovery`, `discovery_selected`. Add `awaiting_address_confirm`, `awaiting_payment`, `awaiting_dineout_details` the same way. The state machine doesn't grow; the dispatcher does.

---

## Phase 1 — Cart-grade UX (the visible win)

**Goal:** Cases 1, 2, 3, 4, 5, 6, 7 + EC-1, EC-2, EC-8, EC-9. After Phase 1, every reply that mentions a SKU shows it the way a human would.

### 1.1 Build the renderer layer

New module: [`apps/api/app/agents/renderer/`](apps/api/app/agents/renderer/) — pure functions, no I/O.

```
apps/api/app/agents/renderer/
  __init__.py              # exports: render_*
  cart.py                  # render_cart_confirmation, render_cart_lines
  discovery.py             # render_discovery_options, render_selected_option
  tracking.py              # render_tracking_status, render_delivered
  greeting.py              # render_welcome, render_goodbye
  chitchat.py              # render_chitchat (variants list, deterministic round-robin)
  stock.py                 # render_out_of_stock, render_substitutes
  payment.py               # render_address_confirm, render_payment_request, render_order_placed
  options.py               # render_options_in_category (CASE 5)
  empty_input.py           # EC-8 / EC-9 short responses
  errors.py                # render_unclear_audio (EC-4), render_service_area (EC-12)
  templates/
    te_in.py               # Telugu code-mixed text fragments + emoji
    en_in.py               # English fragments
    _registry.py           # language → template module dispatch
```

**Key pattern** — every renderer follows this shape:

```python
# cart.py
def render_cart_confirmation(
    cart: ResolvedCart,
    quote: QuoteResult,
    address_label: str | None,
    language: str = "te-IN",
) -> str:
    t = templates_for(language)
    lines = render_cart_lines(cart, quote, language)  # uses cart.line_items
    eta = t.eta_phrase(quote.estimated_delivery_min, quote.estimated_delivery_max, address_label)
    return t.cart_confirmation(
        lines=lines,
        subtotal=quote.subtotal_inr,
        delivery_fee=quote.delivery_fee_inr,
        total=quote.total_inr,
        eta=eta,
        address=address_label,
    )
```

The cart lines themselves come from `cart.line_items` — a structure the upstream layer must now produce.

### 1.2 Make Cart and Quote carry per-line data

[`packages/providers/interface.py`](packages/providers/interface.py) — extend `Cart` and `QuoteResult`:

- `Cart.line_items: list[CartLine]` where `CartLine` carries `display_name`, `brand`, `pack_size_label`, `qty`, `unit_price_inr`, `line_total_inr`, `in_stock`, `requested_size_label` (None unless user asked a different size), `eta_min` per item.
- `QuoteResult.line_items` mirror with computed prices. Keep existing `total_inr`, `delivery_fee_inr`, etc.

Update [`packages/providers/adapters/mock_swiggy_adapter.py`](packages/providers/adapters/mock_swiggy_adapter.py): the `quote()` function already loops items at [line 191](packages/providers/adapters/mock_swiggy_adapter.py#L191); have it emit `line_items` from the same loop. Use the **catalog's `delivery_eta_min` per SKU** (instead of the hardcoded 15-25 at [line 212](packages/providers/adapters/mock_swiggy_adapter.py#L212)) and report `min(per_item_eta)` to `max(per_item_eta) + 5` as the cart ETA.

### 1.3 Stock awareness end-to-end

[`apps/api/app/agents/sku_mapper.py`](apps/api/app/agents/sku_mapper.py:77) — when an item resolves to a SKU with `in_stock: false`, do NOT drop it. Return it on `cart.line_items` with `in_stock=False` and a `substitutes` field populated by a new helper.

New file: [`packages/providers/catalog_helpers.py`](packages/providers/catalog_helpers.py) — pure data lookups over the catalog:

```python
async def find_substitutes(
    *, original_sku_id: str, category: str, limit: int = 2, in_stock_only: bool = True
) -> list[SkuPreview]: ...

async def find_options_in_category(
    *, category: str, limit: int = 3, in_stock_only: bool = True
) -> list[SkuPreview]: ...

async def find_alternative_pack(
    *, base_canonical_key_prefix: str, target_grams: int | None
) -> SkuPreview | None: ...
```

These all call the existing `ProviderRouter` paths — no JSON imports outside the providers package. `SkuPreview` is the lightweight render-friendly DTO (display_name, brand, pack_size_label, price, in_stock).

### 1.4 Pack-size negotiation (CASE 3, no hardcoding)

[`apps/api/app/agents/sku_mapper.py`](apps/api/app/agents/sku_mapper.py) — when the user's `intent.items[i].quantity_with_unit` (e.g., "100g") differs from the matched SKU's `pack_size`, set `CartLine.requested_size_label` to the user's request and let the renderer surface `(100g pack lev, 200g chinna pack vundi)` from the **template**, not from a hardcoded message. The numbers `100g` and `200g` come from `requested_size_label` and `pack_size_label` — no number lives in code.

### 1.5 Substitute suggestions (CASE 4)

When `cart.line_items` is empty and the parser had concrete `intent.items`, call `find_options_in_category(category=intent.items[0].inferred_category)` and pass the result to `render_substitutes`. The renderer says "Bahusha {sub1.display_name} ({sub1.pack_size}, ₹{sub1.price}) leda ..." — every name and number from the lookup.

If no category is inferable, fall back to "popular today" — defined as **top 3 in_stock SKUs by `delivery_eta_min` or by `brand_partnership_weight`** (already a field on `CanonicalSKU` ([models.py:230](packages/core/models.py#L230))).

### 1.6 Welcome with capability menu (CASE 1)

[`renderer/greeting.py`](apps/api/app/agents/renderer/greeting.py): `render_welcome(turn_count, language)` returns the 3-bullet menu the spec shows at [CONVERSATION_FLOW.md:35-44](CONVERSATION_FLOW.md). The bullets ("Groceries — Instamart nundi", etc.) are template-literal because they ARE the offering. Item examples in the parenthetical ("atta, paalu, vegetables") come from a catalog sampler — `find_options_in_category(category="staples_flour", limit=1).display_name_short`, etc., joined with the next two top categories.

### 1.7 Chitchat variants with rotation (EC-1)

[`renderer/chitchat.py`](apps/api/app/agents/renderer/chitchat.py): list of 3-4 variants per context (mid-order, idle, after-success). Pick by Redis INCR counter, same pattern as [`acknowledgement.py:17-21`](apps/api/app/agents/acknowledgement.py#L17-L21). Variants reference *catalog-derived* examples ("Domino's lo BOGO undi" → pulled from `food_catalog.json` offers, NOT from a Python string).

### 1.8 Wire the renderer into pipeline

[`packages/core/pipeline.py`](packages/core/pipeline.py) — replace every `build_confirmation(...)` call site (currently 4 of them) with the appropriate renderer. The non-ORDER branch ([pipeline.py:512-514](packages/core/pipeline.py#L512-L514)) becomes a small dispatcher mapping `(action, state, flow, has_cart)` → renderer call. Keep [`apps/api/app/agents/confirmation.py`](apps/api/app/agents/confirmation.py) only as a deprecation shim that calls the renderer — delete after the pipeline is fully migrated.

### 1.9 Language detection

[`apps/api/app/agents/message_parser.py`](apps/api/app/agents/message_parser.py) already sets `intent.language_detected`. Extend it: if the input is >70% English ASCII tokens and contains no Telugu romanization markers, set `language_detected="en-IN"`. Pass `language=intent.language_detected` through `current["context"]` so every subsequent renderer call uses it.

### Phase 1 verification

- "Hi" (turn 1) → 3-bullet menu welcome with catalog-sampled examples.
- "Hi" (turn 5) → short rotating chitchat redirect, no welcome.
- "I want 2kg onion and 100g paneer" → itemized cart with both lines, "100g pack lev, 200g chinna pack vundi" line on paneer (from `requested_size_label` ≠ `pack_size_label`), subtotal/delivery/total separated, ETA range from catalog.
- "Almonds 250g pampandi" → "almonds dorakaledu" + 2 substitutes from same category (e.g., dry fruits or fallback to popular).
- Send the same flow in English ("I want some flour and 2 milk packets") → reply in English.
- Set one paneer SKU's `in_stock: false` in the JSON → reply offers substitute or asks to wait, not silent drop.

---

## Phase 2 — Real ordering depth (CASE 8, 9, 10, 11, 12 + EC-5, EC-7)

**Goal:** Multi-step checkout, address persistence, payment trigger, tracking, dineout. After Phase 2, the bot can actually *transact* end-to-end with mock providers.

### 2.1 Address persistence (CASE 9)

New table: `Address` ([`packages/core/models.py`](packages/core/models.py)) — `id, ordering_user_id, label, address_line, area, city, pincode, latitude, longitude, is_default, created_at`. Alembic migration. Helpers in [`packages/core/db.py`](packages/core/db.py): `get_default_address(user_id)`, `save_address(user_id, ...)`.

[`packages/core/pipeline.py`](packages/core/pipeline.py) — when the user sends a WhatsApp Location during checkout (sub-flow `awaiting_address_confirm` with no saved address), persist via `save_address`. When they type an address ("Flat 302, Kondapur, Hyderabad, 500084"), parse it deterministically (regex for pincode, comma split) and save with `latitude=None, longitude=None` — provider will geocode at execute time.

### 2.2 Multi-step checkout state machine (CASE 8)

Extend conversation context flows (no new top-level states needed):

```
AWAITING_CONFIRMATION + flow="cart_review"           # cart shown, waiting for "confirm"/"add"
AWAITING_CONFIRMATION + flow="awaiting_address_confirm"  # "Same address aana?"
AWAITING_CONFIRMATION + flow="awaiting_payment_method"   # "UPI or COD?"
AWAITING_APPROVAL    + flow="awaiting_payment_complete"  # UPI request sent, waiting for webhook
EXECUTING            + (no flow needed)
```

Pipeline dispatcher routes by `(state, flow, intent.action)`. Each transition writes the next-step renderer output as `confirmation_text` so a Redis-loss + rehydrate keeps the user oriented.

### 2.3 Mock UPI request (CASE 8)

No real Razorpay yet. Add [`apps/api/app/agents/payment.py`](apps/api/app/agents/payment.py): `create_mock_upi_request(amount, payer_handle, family_id) -> PaymentRequest`. Persist a row in the existing `PaymentRequest` table ([models.py:131-166](packages/core/models.py#L131-L166)) with `status="pending"`. Render the spec's "Mee total ₹{total} ki UPI request pampancha {payer}@upi" via `renderer/payment.py`.

Add a debug endpoint in [`apps/api/app/api/routes.py`](apps/api/app/api/routes.py): `POST /debug/payment/{payment_request_id}/approve` that flips status to "paid" and triggers the AWAITING_APPROVAL → EXECUTING transition (calls `executor.execute_order`). For the demo, you can hit this from curl. When Razorpay is integrated later, the real webhook calls the same code path.

### 2.4 COD path (EC-7)

`PaymentRequest` gets a new column `payment_method: str` (default "upi"). When the user says "cash" / "COD" mid-checkout, set `payment_method="cod"`, skip the UPI request, persist `PaymentRequest(status="cod_pending")`, transition straight to EXECUTING. Renderer: `render_cod_acknowledged(total, language)`.

### 2.5 Tracking backend (CASE 12 + EC-6)

[`apps/api/app/agents/tracking.py`](apps/api/app/agents/tracking.py): `get_active_order_for_user(user_id) -> Order | None` (latest where `status NOT IN ("delivered", "cancelled", "failed")`), `get_order_by_short_id(suffix, user_id) -> Order | None` (matches on the last-6 of `provider_order_id`).

Mock provider needs to advance order status over time. Add a Redis-backed status simulator: when `Order.placed_at` is N min ago, status = `confirmed` (0-2), `preparing` (2-8), `out_for_delivery` (8-15), `delivered` (15+). Rider name + phone from a small fixed roster in `mock_riders.json` (mock data, NOT hardcoded in Python — pick by `Order.id.bytes[0] % len(roster)`). When the real Swiggy MCP arrives, this gets swapped for a tracking call; the renderer is unchanged.

`render_tracking_status(order, rider, eta_minutes, language)` produces the spec format including 🛵 / 📦 / ⏱️.

### 2.6 Order cancellation by stage (EC-5)

Pipeline TRACK/CANCEL handler: distinguish between confirmation-stage cancel (today's behavior) and post-placed cancel. For post-placed:

- `status in ("pending", "confirmed", "preparing")` → cancellable. Update Order, mock-refund the PaymentRequest, render "Cancel ayyindi, refund 2 hours lo".
- `status in ("out_for_delivery", "delivered")` → not cancellable. Render the "Sorry, already out for delivery..." line from the spec.

### 2.7 Dineout slots (CASE 11)

`dineout_catalog.json` doesn't have time slots today. Two options, pick one:

**Option A (fastest):** Add a `slot_template` field per restaurant in the JSON: `{"weekday": ["19:00","19:30","20:00",...], "weekend": [...]}`. The dineout adapter generates a `DineoutSlot` list for the requested date by reading the template. Booking flow uses sub-flow `awaiting_dineout_details` → `awaiting_dineout_slot_pick` → `awaiting_dineout_confirm`. Renderer at each step.

**Option B (preferred for MVP):** Synthesize slots deterministically from `restaurant.id` + date — every restaurant gets 7:00, 7:30, 8:00, 8:30, 9:00, 9:30 PM with `available=True` unless `(hash(restaurant_id + date + slot)) % 5 == 0` (mark "booked"). Zero new mock data needed. Booking just persists an `Order` with `cart_items={"type":"dineout","restaurant_id":..., "slot":...}` and renders the spec confirmation.

Use Option B for now; pivot to A once the demo settles.

### 2.8 Empty / unclear / goodbye (EC-8, EC-9)

[`renderer/empty_input.py`](apps/api/app/agents/renderer/empty_input.py): handle `intent.action == "UNCLEAR"` AND short body (≤2 chars or only emoji) with "Em kavali? 😊 Order cheyyali, track cheyyali, leda inka emina?". Goodbye words ("bye", "thanks", "tarwata") → goodbye renderer + transition to IDLE (with state preserved per Polish 1).

### Phase 2 verification

- "I want atta and milk" → cart → "checkout" → address-confirm prompt with saved address (or location ask if first order) → "same" → UPI request renderer → curl the debug endpoint → order placed renderer with tracking ID.
- After 5 minutes, "track" → status `preparing` rendered with rider details.
- After 12 simulated minutes, "track" → `out_for_delivery`.
- Mid-preparation "cancel order" → cancel succeeds, refund line shown.
- Out-for-delivery "cancel" → refused with the spec's exact tone.
- "Saturday 4 mandi veg dineout" → 2 dineout options with synthesized slots → "1, 8 PM" → booking confirmation.

---

## Phase 3 — Intelligence layer

**Goal:** EC-10 (mamulu), EC-11 (anomaly), EC-12 (service area), EC-3 (voice burst), EC-4 (audio quality).

### 3.1 Mamulu / repeat-order detection (EC-10)

[`apps/api/app/agents/personalization.py`](apps/api/app/agents/personalization.py): `get_usual_order(user_id, lookback_days=14, min_repeats=3) -> Cart | None`. Query `Order` rows for the user, group by canonical-key fingerprint, find any combination of items repeated 3+ times. Return as a synthesized Cart.

In pipeline, when intent is `ORDER` and the parsed items overlap heavily with the user's usual (≥80% by canonical-key Jaccard), prepend "Mamulu order pedathana?" and render the usual cart for confirmation. User says "yes" → checkout flow with the usual cart.

### 3.2 Large-cart anomaly (EC-11)

[`personalization.py`](apps/api/app/agents/personalization.py): `get_normal_cart_total(user_id) -> int | None` (median total of last 10 orders). At cart-confirmation render time, if `quote.total_inr > 2 * normal_total`, the renderer adds the spec's "Idi mamulu kanna pedda order. Sure aana?" line. No auto-block — just transparent surfacing.

This change is one branch in `render_cart_confirmation`. The threshold itself comes from data, not config.

### 3.3 Service-area gate (EC-12)

New mock file: [`packages/providers/data/service_area.json`](packages/providers/data/service_area.json) — list of supported pincodes / lat-lng polygons (start with the spec's "Hyderabad city center, Gachibowli, Madhapur, Kondapur, Banjara Hills, Hitec City, Kukatpally" — encode pincodes 500001 through 500084 + the listed area names). Loader + `is_in_service_area(location) -> tuple[bool, str | None]` that returns `(False, "Patancheru")` for refusal.

When a location arrives during checkout, run the gate before persisting. Renderer: spec's "Sorry, mee area '{area_name}' lo Bolo ee time lo deliver cheyyaledu". The list of supported areas in the rejection message is **also rendered from the JSON**, not from a Python string.

### 3.4 Voice-burst debouncing (EC-3)

Two-part change:

1. [`apps/api/app/api/webhook.py`](apps/api/app/api/webhook.py) — when a message arrives, set Redis key `pending:{user_id}` to message_id with 5s expiry. If a key already exists, append the new message to a list in Redis instead of enqueueing a job.
2. [`apps/api/app/worker.py`](apps/api/app/worker.py) — add a separate "debounce drain" loop that, every 1s, scans `pending:*` keys older than 4s, reads the message list, concatenates audio transcripts (or text bodies), and enqueues ONE job. Single reply.

Configurable via `settings.voice_burst_debounce_sec` (default 4).

### 3.5 Low audio confidence (EC-4)

[`apps/api/app/agents/transcriber.py`](apps/api/app/agents/transcriber.py) already returns `transcription.confidence`. In [`worker.py`](apps/api/app/worker.py) audio path ([line 135](apps/api/app/worker.py#L135)), when `confidence < 0.55`, skip the pipeline and return `render_unclear_audio(language)` directly. After two consecutive low-confidence attempts (track in Redis `bad_audio:{user_id}` counter, 5min TTL), suggest typing.

### Phase 3 verification

- 4 orders of `atta + milk` over 10 days → next "atta paalu teesuko" → "Mamulu order pedathana?" + saved cart.
- Order 5x normal value → cart confirm includes "Idi mamulu kanna pedda order" line.
- Send WhatsApp location for Patancheru (pincode 502319) → service-area refusal with supported-areas list rendered from JSON.
- Send 3 voice notes within 8 seconds → bot replies once, combining all 3 into one cart understanding.
- Send a 0.3-second mumble → "Sorry, voice clear ga vinabadaledu" + retry suggestion.

---

## Phase 4 — Cleanup, tests, demo dry-run

- Delete the old [`confirmation.py`](apps/api/app/agents/confirmation.py) once all call sites use the renderer. Move TRACK/DISCOVER acks into `renderer/chitchat.py` since they're not really confirmations.
- Unit tests:
  - `test_renderer_cart_with_oos_substitute` — table-driven, varies `in_stock`, `requested_size_label`, language.
  - `test_personalization_mamulu_threshold` — seed N orders, assert the right cart surfaces.
  - `test_service_area_rejection_lists_supported_areas_from_json` — mutate JSON, assert renderer reflects.
  - `test_voice_burst_debounce_combines_messages` — fake 3 webhooks within 4s, assert single worker job.
- Integration test driving the full S1 dialogue from CONVERSATION_FLOW.md cases 1-3-5-6-7-8-12.
- Demo dry-run: walk through the spec in order, screen-record, fix any deviation.

---

## What stays out of scope (Swiggy MCP integration)

The provider layer is already split: `ProviderRouter` ([packages/providers/router.py](packages/providers/router.py)) calls adapters that conform to `interface.py`. When Swiggy MCP releases:

1. New file `packages/providers/adapters/swiggy_mcp_adapter.py` implementing `GroceryProvider`, `FoodProvider`, `DineoutProvider`.
2. Flip a setting `provider_mode = "mcp"` (vs. current `"mock"`) in [`apps/api/app/core/config.py`](apps/api/app/core/config.py).
3. The renderer, pipeline, state machine, and templates **don't change**. That's the point of doing Phase 1's renderer split now.

---

## What this plan deliberately does NOT do

- No prompt engineering on the LLM parser — the parser already extracts items + quantities. The bug is downstream.
- No new state-machine top-level states. We extend `context.flow` only.
- No schema migration for the renderer work in Phase 1. Address table in Phase 2 needs one Alembic migration. That's it.
- No real Razorpay until Phase 2.3+ is demoed and approved with mock UPI.
- No Hindi/Kannada/Tamil templates yet. The architecture supports them; pick when you have a translator review the te-IN strings.

---

## File-change summary (high-level diff)

| Phase | Files added | Files modified | Lines (rough) |
|---|---|---|---|
| 1 | `agents/renderer/*` (~10 files), `providers/catalog_helpers.py` | `interface.py`, `mock_swiggy_adapter.py`, `sku_mapper.py`, `pipeline.py`, `message_parser.py` | +900 / -150 |
| 2 | `agents/payment.py`, `agents/tracking.py`, `mock_riders.json`, Alembic migration for Address | `models.py`, `pipeline.py`, `executor.py`, `routes.py`, `webhook.py`, `dineout` adapter | +700 / -50 |
| 3 | `agents/personalization.py`, `service_area.json` | `webhook.py`, `worker.py`, `transcriber.py` (low-conf branch), renderer cart (anomaly line) | +400 / -20 |
| 4 | tests under `tests/renderer/`, `tests/integration/` | delete `confirmation.py` | +600 / -120 |

Roughly 2,500 net new lines, mostly templates + tests. No restructure of core paths.

---

## Demo script (after Phase 1 + Phase 2)

> "Watch this whole order from one Telugu sentence. *Atta rendu kilolu, milk 2, paneer 100g kavali.* Bot returns an itemized cart with the 200g paneer flagged because there's no 100g pack — exactly the spec — total ₹X, ETA from catalog, address pulled from my profile. *Sare* — UPI request to my husband's handle. He approves on his phone. Order placed, tracking ID. Five minutes later: *track* — status preparing, rider name. Try to cancel: refused because already out. Now I'll close WhatsApp, restart Redis live, and continue: *track* — same answer, because the source of truth is Postgres, not Redis. The bot greets only on first message; small talk doesn't reset the cart. This is the difference between a demo and a product."

---

**Next step after you read this:** confirm Phase 1 scope. Phase 1 alone takes the visible-bug pain to zero and gives you the demo opener (cart + substitutes + language match). Phases 2/3 turn it into a real ordering tool.
