# foodleaf — Architecture (Data Model, APIs, Topology)

> **When to load:** When working on database schemas, API endpoints, infrastructure setup, deployment, or system-wide refactors. Pair with `00_PROJECT_CONTEXT.md`.

---

## System Topology

```
                                  ┌──────────────────┐
                                  │   WhatsApp       │
                                  │   (Amma's phone) │
                                  └────────┬─────────┘
                                           │
                                  ┌────────▼─────────┐
                                  │   Gupshup BSP    │
                                  │   (webhook)      │
                                  └────────┬─────────┘
                                           │ webhook POST
                                  ┌────────▼─────────┐
                                  │  /webhook        │ ← FastAPI on Railway
                                  │  (200 + queue)   │
                                  └────────┬─────────┘
                                           │
                                  ┌────────▼─────────┐
                                  │  Upstash Redis   │ ← idempotency + queue
                                  │  - dedup keys    │
                                  │  - job queue     │
                                  │  - convo state   │
                                  └────────┬─────────┘
                                           │
                       ┌───────────────────┼─────────────────────┐
                       │                   │                     │
              ┌────────▼────────┐  ┌──────▼──────┐  ┌───────────▼────────┐
              │ Voice Pipeline  │  │ Care Monitor│  │ Webhook Responders │
              │ Worker          │  │ Worker      │  │ (order updates)    │
              └────────┬────────┘  └──────┬──────┘  └───────────┬────────┘
                       │                  │                     │
        ┌──────────────┼──────────────┐   │                     │
        │              │              │   │                     │
   ┌────▼────┐    ┌───▼────┐    ┌────▼───▼────────┐       ┌─────▼─────┐
   │ Sarvam  │    │ Claude │    │ Neon Postgres   │       │  Gupshup  │
   │ STT/TTS │    │ Sonnet │    │ + pgvector      │       │  outbound │
   │         │    │ /Haiku │    │ + TimescaleDB   │       │           │
   └─────────┘    └────────┘    │   ext           │       └───────────┘
                                └────────┬────────┘
                                         │
                              ┌──────────▼──────────┐
                              │ ICommerceProvider   │
                              │ (interface)         │
                              └──────────┬──────────┘
                                         │
                              ┌──────────▼──────────┐
                              │   SwiggyAdapter     │
                              │   (MCP client)      │
                              └──────────┬──────────┘
                                         │
                              ┌──────────▼──────────┐
                              │   Swiggy MCP        │
                              │   Food/Instamart/   │
                              │   Dineout           │
                              └─────────────────────┘

   Family Payer (Kiran):
   Browser → Vercel (Next.js PWA) → Same Postgres → Razorpay/Stripe webhooks
```

### Hosting Layout

| Component | Service | Region |
|---|---|---|
| FastAPI app + workers | Railway.app | Singapore (closest to India) |
| Postgres + pgvector | Neon.tech | AWS Mumbai |
| Redis | Upstash | AWS Mumbai |
| Object storage (audio) | Cloudflare R2 | Auto (global edge) |
| Adult-child PWA | Vercel | Singapore + edge |
| WhatsApp BSP | Gupshup | India |
| Sarvam APIs | Sarvam Cloud | India |
| Claude API | Anthropic API | US (acceptable for non-PII reasoning) |

**Data residency note:** All identifying data (names, phones, addresses, order history, voice recordings) lives in Mumbai/India region. Claude only sees redacted intent payloads, never raw PII. Voice files stay in R2 with India-pinned metadata.

---

## Data Model — Core Tables

### Family
The unit of billing and payment routing. One family can have many ordering users and one or more configured payers.

```
families
  id (uuid, pk)
  display_name (text)              # "Sharma Family"
  default_payer_user_id (fk → users) # default family payer
  primary_locale (text)             # "te-IN"
  city (text)                       # "Hyderabad"
  approval_threshold_inr (int)      # default 1500
  care_features_enabled (bool)
  created_at, updated_at
```

### Users
```
users
  id (uuid, pk)
  family_id (fk)
  role (enum: 'ordering_user' | 'payer' | 'both')
  relationship_label (text, nullable) # "parent", "sibling", "spouse"; config only, never a user-tier label
  display_name (text)               # "Amma" or "Lakshmi Sharma"
  phone_e164 (text, unique)
  whatsapp_phone_e164 (text)
  preferred_language (text)         # "te-IN", "ta-IN", "hi-IN"
  voice_print_id (text, nullable)   # for multi-member disambiguation
  dietary_constraints (jsonb)       # {"diabetic": true, "vegetarian": true, "no_garlic": false}
  brand_preferences (jsonb)         # {"milk": "heritage", "atta": "aashirvaad_select"}
  active (bool)
  created_at, updated_at
```

### Family Payer Configuration & Payment Requests
We do NOT custody money. Razorpay UPI Collect handles charging the designated payer at order time.

```
family_payers
  id (uuid, pk)
  family_id (fk)
  user_id (fk → users)              # the family member who will be charged
  upi_handle (text)                  # "kiran@okaxis"
  is_default_payer (bool)            # primary payer for this family
  category_routing (jsonb)           # {"groceries": user_id_1, "food": user_id_2, "medicine": user_id_3}
  auto_approve_threshold_inr (int)   # 0 = disabled; if >0, charges below this auto-approve silently after 30-day trust period
  trust_started_at (timestamp)       # when this payer was added; auto-approve only kicks in after 30 days
  active (bool)
  created_at, updated_at

payment_requests (TimescaleDB hypertable)
  id (uuid, pk)
  family_id, ordering_user_id, payer_user_id
  related_order_id (fk → orders, nullable until order placed)
  related_voice_session_id (fk)
  amount_inr (int)
  upi_handle_charged (text)
  razorpay_request_id (text)         # Razorpay UPI Collect ID
  status (enum: 'initiated' | 'sent_to_payer' | 'approved' | 'rejected' | 'expired' | 'paid' | 'failed')
  auto_approved (bool)               # was this within auto-approve threshold?
  initiated_at, payer_responded_at, paid_at, expired_at
  failure_reason (text, nullable)
  ts
```

**Critical correctness rules:**
- A payment_request is created BEFORE provider order is placed
- Provider checkout only fires after payment_request.status = 'paid' OR 'auto_approved'
- If payment_request expires (90 sec timeout) or rejected, no order is placed; ordering user informed gracefully
- Idempotency: same voice_session_id can never produce two payment_requests (DB unique constraint)

### Interaction Sessions (the goldmine)
Every WhatsApp text or voice interaction. Used for retraining, debugging, audit.

```
voice_sessions (TimescaleDB hypertable)
  id (uuid, pk)
  family_id, ordering_user_id
  whatsapp_message_id (text, unique)   # idempotency
  input_mode (enum: 'text' | 'voice')
  raw_text (text, nullable)             # populated for text messages
  audio_r2_key (text, nullable)         # R2 path for voice messages
  audio_duration_sec (numeric, nullable)
  transcription_raw (text, nullable)
  normalized_text (text)                # text input or STT result used for parsing
  language_detected (text)
  transcription_confidence (numeric, nullable)
  parsed_intent (jsonb)                # full ParsedIntent struct
  resolved_cart (jsonb)                # full ResolvedCart struct
  conversation_state (text)            # IDLE / PARSING / AWAITING_CONFIRMATION / ...
  pipeline_latency_ms (int)
  outcome (enum: 'order_placed' | 'cancelled' | 'amended' | 'failed' | 'still_pending')
  failure_reason (text, nullable)
  ack_message_sent (bool)
  created_at, updated_at
```

### SKU Catalog (mirror)
We mirror available provider catalogs for the ordering user's city, refreshed every 6 hours where APIs allow it. Enables fast pgvector search before hitting live provider APIs/MCP; manual fallback catalogs can seed the same table.

```
canonical_skus
  id (uuid, pk)
  canonical_key (text, unique)         # "aashirvaad_select_atta_5kg"
  display_name_en (text)
  display_names_local (jsonb)          # {"te-IN": ["godi pindi", "atta", "गोధుమ పిండి"]}
  category (text)                      # "staples_flour"
  subcategory (text)
  brand (text)                         # "aashirvaad"
  pack_size (text)                     # "5kg"
  typical_price_band_min_inr (int)
  typical_price_band_max_inr (int)
  embedding (vector(1024))             # pgvector — multilingual embedding
  brand_partnership_weight (numeric)   # 0.0-1.0; 0 = no partnership
  active (bool)
  last_seen_at
  created_at, updated_at

provider_sku_mappings
  id, canonical_sku_id (fk)
  provider (enum: 'swiggy_instamart_mcp' | 'swiggy_food_mcp' | 'swiggy_dineout_mcp' | 'ondc' | 'manual_ops')
  provider_sku_id (text)               # provider SKU/product ID, ONDC item ID, or manual catalog ID
  provider_metadata (jsonb)
  city (text)
  available (bool)
  last_price_inr (int)
  last_seen_at
```

### Orders
Linked to provider order IDs. Status is mirrored from provider via tracking.

```
orders
  id (uuid, pk)
  family_id, ordering_user_id
  voice_session_id (fk)
  payment_request_id (fk → payment_requests)   # must be 'paid' before checkout fires
  provider (text)                      # 'swiggy_instamart' etc.
  provider_order_id (text)
  cart_items (jsonb)                   # full ResolvedCart at time of order
  total_inr (int)
  status (enum: 'pending_payment' | 'pending' | 'confirmed' | 'preparing' | 'out_for_delivery' | 'delivered' | 'cancelled' | 'failed')
  required_explicit_approval (bool)    # true if amount > soft-approval threshold
  delivery_address_snapshot (jsonb)
  placed_at, delivered_at
  failure_reason (text, nullable)
  created_at, updated_at
```

### Care Signals
Opt-in anomaly flags surfaced to the configured family payer/caregiver.

```
care_signals (TimescaleDB hypertable)
  id, family_id, affected_user_id
  signal_type (enum: 'silence' | 'duplicate_order' | 'cognitive_pattern' | 'upi_rejection_pattern' | 'payer_balance_low' | 'delivery_failed' | 'unusual_value' | 'unusual_hour')
  severity (enum: 'info' | 'warn' | 'urgent')
  payload (jsonb)
  sent_to_family_at (timestamp, nullable)
  acknowledged_at (timestamp, nullable)
  ts
```

### Conversation State
Per-user active session state. Lives in Redis primarily, mirrored to Postgres for durability.

```
Redis key: convo:{ordering_user_id}
Value (json):
  state: 'IDLE' | 'PARSING' | 'AWAITING_CONFIRMATION' | 'EXECUTING' | 'COMPLETE'
  active_voice_session_id: uuid
  pending_cart: ResolvedCart | null
  expires_at: timestamp
  amendment_count: int
TTL: 30 minutes (re-extended on each message)
```

### Vocabulary Map (Telugu → SKU category)
The dictionary that handles "perugu" vs "majjiga" vs regional terms. Hand-curated initially, expanded from voice sessions.

```
vocabulary_terms
  id, term (text)
  language (text)                      # "te-IN"
  region (text, nullable)              # "telangana" / "coastal_andhra" / null
  maps_to_category (text)
  maps_to_brand (text, nullable)
  default_pack_size (text, nullable)
  notes (text)                         # "regional/legacy term, also means buttermilk in coastal"
  confidence (numeric)
```

### Brand Partnerships
Drives weighted SKU recommendations.

```
brand_partnerships
  id, brand_name
  contract_status (enum: 'active' | 'paused' | 'expired')
  weight_multiplier (numeric)          # 1.0 = no boost; 1.5 = 50% rank boost
  per_order_payout_inr (int)
  category_scope (text[])              # which categories this applies to
  region_scope (text[])                # which states/cities
  contract_start, contract_end
  total_orders_attributed
  total_payout_inr
```

---

## Embeddings Strategy

For Telugu/regional voice → SKU matching, we use multilingual sentence embeddings.

**Model:** `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` OR Sarvam's embedding endpoint when stable. 768-dim minimum; we pad to 1024 for future flexibility.

**What we embed:**
- For each canonical SKU: concatenate `[brand, display_name_en, all display_names_local entries, category]`
- Single embedding per SKU stored in `canonical_skus.embedding`

**Search flow:**
1. User says "rendu kilo atta"
2. Parser extracts items: `["atta"]`
3. For each item, embed the term + family's region
4. pgvector cosine similarity, top 5
5. Re-rank by: previous purchase preference (×2.0), brand partnership weight (×1.0-1.5), price-band fit (×1.0-1.2)
6. Top 1-3 sent to Confirmation Agent

**Cache layer:** Redis cache keyed by `family_id:item_text` → resolved SKU. After 4 weeks of usage, ~70% of items skip embedding search entirely.

---

## API Contracts (Internal)

### `POST /webhook/whatsapp` (Gupshup → us)
```
Request body (Gupshup format)
Response: 200 OK immediately, queue job to Redis

Action:
1. Verify Gupshup signature
2. Extract message_id
3. SETNX dedup:msg:{message_id} 1 EX 86400 → if false, return 200 and stop
4. Push to Redis queue message_pipeline:incoming with `input_mode=text|voice`
5. Return 200
```

### `POST /webhook/razorpay`, `POST /webhook/stripe`
Payment webhook handling for UPI Collect payment requests, refunds, and NRI Stripe payment flows.

### `POST /webhook/swiggy/order-status` (if Swiggy MCP supports webhooks; else poll via track_order)
Updates `orders.status` based on tracking.

### Family-Member PWA APIs (Next.js → FastAPI)

| Endpoint | Purpose |
|---|---|
| `GET /api/family/me` | Get my family + members + payer config |
| `POST /api/family/onboard` | Initial onboarding flow |
| `POST /api/family/add-member` | Add another family member by phone |
| `PUT /api/family/payer-config` | Set default payer, category routing, auto-approve threshold |
| `GET /api/orders?member_id=&from=&to=` | Order history |
| `GET /api/payment-requests?status=` | UPI request history (paid, rejected, pending) |
| `GET /api/voice-sessions?member_id=` | Voice session log (transcripts only, audio behind 2nd-tap auth) |
| `GET /api/care-signals/active` | Unacknowledged signals (only if family opted-in to care features) |
| `POST /api/care-signals/{id}/ack` | Acknowledge a signal |
| `PUT /api/family/{id}/settings` | Update threshold, language, care opt-in, etc. |
| `PUT /api/member/{id}/preferences` | Update brand prefs, dietary constraints |
| `POST /api/orders/{id}/approve` | Soft-approve a flagged order (triggers UPI request) |
| `POST /api/orders/{id}/cancel` | Cancel a pending order |
| `POST /api/payment-requests/{id}/retry` | Retry a failed UPI request |

All authenticated via JWT issued after WhatsApp OTP verification. No payment-related auth needed since we never custody funds — Razorpay handles all payment auth via UPI.

---

## ICommerceProvider Interface (Pseudocode)

```python
class CanonicalSKU:
    canonical_key: str
    display_name: str
    pack_size: str
    estimated_price_inr: int
    provider_specific_id: str

class CartItem:
    canonical_sku: CanonicalSKU
    quantity: int
    notes: Optional[str]

class CartHandle:
    provider: str
    provider_cart_id: Optional[str]   # if stateful cart
    items: List[CartItem]
    expires_at: Optional[datetime]

class QuoteResult:
    cart_handle: CartHandle
    subtotal_inr: int
    delivery_fee_inr: int
    taxes_inr: int
    total_inr: int
    estimated_delivery_min: int
    estimated_delivery_max: int

class OrderResult:
    success: bool
    provider_order_id: Optional[str]
    final_total_inr: Optional[int]
    failure_code: Optional[str]
    failure_reason: Optional[str]

class ICommerceProvider(Protocol):
    name: str
    kind: Literal["mcp", "ondc", "direct_api", "manual_ops"]
    supported_categories: List[str]

    async def search_skus(
        self, query_text: str, language: str, location: Location, limit: int
    ) -> List[CanonicalSKU]: ...

    async def check_availability(
        self, sku_ids: List[str], location: Location
    ) -> Dict[str, AvailabilityResult]: ...

    async def assemble_cart(
        self, items: List[CartItem], location: Location
    ) -> CartHandle: ...

    async def quote_cart(
        self, cart: CartHandle
    ) -> QuoteResult: ...

    async def execute_checkout(
        self, cart: CartHandle, payment_ref: PaymentRef, customer: CustomerProfile
    ) -> OrderResult: ...

    async def track_order(
        self, provider_order_id: str
    ) -> OrderStatus: ...

    async def cancel_order(
        self, provider_order_id: str
    ) -> CancellationResult: ...
```

### SwiggyAdapter — How It Implements The Interface

| Interface method | Swiggy MCP tools used |
|---|---|
| `search_skus` | `search_products` (Instamart) — but only when local pgvector cache misses |
| `check_availability` | `search_products` filtered by IDs, plus current cart inspection |
| `assemble_cart` | `update_cart` (multiple calls if stateful) or in-memory CartHandle |
| `quote_cart` | `get_cart` for total/delivery/taxes |
| `execute_checkout` | `checkout` |
| `track_order` | `track_order` |
| `cancel_order` | (Swiggy's MCP doesn't appear to expose cancel — use Swiggy support flow + mark internally) |

For Food MCP and Dineout MCP, build separate `SwiggyFoodAdapter` and `SwiggyDineoutAdapter` implementing slightly different sub-interfaces (food doesn't have stateful cart in same way; dineout is reservation, not order).

### Non-MCP Provider Adapters

foodleaf must continue functioning even if Swiggy MCP access is delayed, rate-limited, or revoked. These adapters use the same `ICommerceProvider` contract, so agents never care where the order is fulfilled.

| Adapter | Purpose | MVP behavior |
|---|---|---|
| `ManualOpsAdapter` | Human-assisted fallback for early families and provider outages | Creates an ops task with cart, address, payer confirmation, and SLA; founder/ops places order manually |
| `ONDCAdapter` | Coverage for non-Swiggy cities and MCP-independent ordering | Start as a spike with catalog/search/quote; checkout can remain gated until reliability is proven |
| `DirectProviderAdapter` | Future direct APIs from grocers/restaurants/cloud kitchens | Same cart/quote/checkout contract as MCP providers |

Provider router priority for MVP: Swiggy MCP when healthy → ManualOpsAdapter for supported early families → ONDCAdapter where a reliable seller exists. Manual fallback is slower but keeps the promise alive during beta.

---

## Failure Modes and Handlers (System-Level)

### Sarvam STT down or low confidence
- Confidence <50%: ask "Amma, malli cheppandi, vinabadaledu"
- Confidence 50-70%: proceed but always re-confirm before checkout
- Sarvam API down: fall back to Gemini 2.5 Flash. Tag voice_session with `fallback_used=gemini` for monitoring.

### Claude API down
- Retry with exponential backoff (3 attempts: 200ms, 800ms, 2000ms)
- After 3 fails, send: "Amma, technical problem vachhindi, malli try chesthana?"
- Log incident, alert via PagerDuty/email (free tier).

### Swiggy MCP down or schema-changed
- Adapter catches exceptions, surfaces typed error
- Provider router tries the next eligible adapter before failing the session
- If manual fallback is available: ordering user hears/sees "Konchem manual ga confirm chesthanu, 10 nimishalu pattachu" and an ops task is created
- If no fallback is available: gracefully inform ordering user "Amma, ee time lo provider problem undi, konchem agandi"
- Interaction session marked `outcome=failed` only after all eligible adapters fail
- Auto-retry every 15 min for up to 2 hours; if recovered, agent pings ordering user: "Amma, ippudu try cheyamani?"

### UPI Request Failure / Payer Doesn't Approve
- Razorpay UPI Collect sent; payer has 90 seconds to approve in their UPI app
- If approved: order proceeds to provider checkout
- If rejected: voice to ordering user "Sorry, payment approve avvaledhu, malli try chesthana?"; order marked cancelled
- If timeout (90s): voice "Payment time out ayyindi, [payer name] ki call cheyandi maybe?"; order parked for 6 hours, can retry from PWA
- Care signal sent to family if 3+ rejections in 24h (could indicate payer's UPI issue or family conflict)

### Idempotency violation (duplicate webhook)
- Redis SETNX check — silent skip, return 200
- Logged but not alerted (this is expected operational noise)

### Pipeline 12-second hard timeout
- Kill in-flight pipeline
- Send graceful failure to ordering user
- Mark voice_session `outcome=failed`, `failure_reason='pipeline_timeout'`
- Send care signal to configured family payer/caregiver if 3+ timeouts in same family in 24h

---

## Observability Requirements

Even at MVP, these are non-negotiable.

| Metric | Tool | Alert threshold |
|---|---|---|
| End-to-end pipeline latency p50/p95/p99 | Self-hosted Grafana on Railway, free tier | p95 > 7s |
| STT confidence distribution | Logged to Postgres, dashboard query | >20% sessions <60% confidence |
| Order success rate | Postgres metric | <90% over rolling 1h |
| UPI Request approval rate | Postgres metric | <80% over rolling 24h (signals payer fatigue or trust issue) |
| UPI Request approval latency p50/p95 | Postgres | p95 > 60 seconds |
| Provider error rate by adapter | Postgres + Grafana | >5% error rate |
| WhatsApp delivery failures (Gupshup) | Gupshup dashboard + webhook capture | >2% failure |
| Daily active families | Postgres | Sudden drop >30% day-over-day |
| Care signals generated vs acknowledged | Postgres | Acknowledge rate <40% |
| Sarvam API error rate | App logs | >2% errors |
| Claude API error rate | App logs | >2% errors |

Use **Sentry** (free tier) for exception tracking. Use **Better Stack / Logtail** (free tier) for log aggregation. Set Slack/email alerts for critical thresholds.

---

## Security & Trust

### Voice Recording Storage
- All voice notes encrypted at rest in R2 (AES-256, R2-managed keys)
- Access logged
- Auto-delete after 90 days unless retention opt-in
- Never shared with third parties or used for model training without explicit consent

### PII in Claude Calls
- We send Claude **redacted** payloads only:
  - Phone numbers replaced with `<phone>`
  - Names replaced with `<ordering_user_name>` / `<payer_name>`
  - Addresses replaced with `<delivery_address>` placeholder
- Claude returns structured intent/response which we re-hydrate locally
- This keeps sensitive PII inside India region

### Payment Money
- Funds move through Razorpay UPI Collect or Stripe Connect; foodleaf never stores a prefunded balance
- We never have direct custody → no PPI license needed at MVP
- Reconciliation job runs hourly to verify payment_requests, provider orders, and refunds match

### Family Payer Authentication
- Magic link via WhatsApp OTP
- 24-hour session JWT
- High-risk actions (change payer routing, remove a family member, alter care settings) require fresh re-auth

### Rate Limiting
- Per-family: max 30 voice sessions/hour (catches phone-stuck-in-pocket scenarios)
- Per-IP on PWA: standard rate limits via Vercel/Cloudflare
- Per-user order velocity: max 4 orders/day at MVP (relaxable per family)

---

## Deployment & CI/CD (MVP)

- **Repo:** Single monorepo on GitHub
  ```
  /apps
    /api          (FastAPI app, all webhooks + worker entrypoints)
    /pwa          (Next.js 14 family payer app)
  /packages
    /agents       (the 6 agent implementations)
    /providers    (ICommerceProvider + SwiggyAdapter)
    /core         (data models, db client, redis client, types)
    /vocab        (vocabulary maps, regional Telugu dictionaries)
  /infra
    /migrations   (Postgres migrations via Alembic)
  /scripts
    /catalog_sync (job to mirror Swiggy SKU catalog)
    /care_monitor (the Care Monitor Agent runner)
  ```

- **CI:** GitHub Actions — lint, type-check, unit tests on every PR
- **CD:** Railway auto-deploys main branch; Vercel auto-deploys PWA
- **DB migrations:** Alembic, run on deploy
- **Secrets:** Railway env vars + Vercel env vars; never commit
- **Feature flags:** Simple table in Postgres, hot-reloadable

---

## Cost Model (Monthly, MVP at 100 families)

| Item | Cost |
|---|---|
| Railway (4 services: api, voice-worker, care-worker, catalog-sync) | ₹2,500 |
| Neon Postgres (Pro plan, India region) | ₹1,500 |
| Upstash Redis | ₹0 (free tier) |
| Cloudflare R2 (50GB voice + audio) | ₹400 |
| Vercel PWA | ₹0 (free tier) |
| Gupshup WhatsApp messages | ₹500-900 (mostly free service window; ~₹0.13 per care alert) |
| Sarvam APIs | ₹0 (using Startup Programme credits) |
| Anthropic Claude API | ₹3,000-5,000 |
| Razorpay txn fees | ~2% of successful UPI/Stripe payments (passed to user) |
| Sentry, Logtail | ₹0 (free tier) |
| Domain + email | ₹200 |
| **Total** | **₹8,000-10,500/month** |

At 1,000 families: roughly ₹35,000-50,000/month before AWS migration. AWS Activate covers this.

---

## What Cursor/Antigravity Should Do When Working In This Codebase

1. Always read `00_PROJECT_CONTEXT.md` first
2. For data model changes: read this file's "Data Model" section + propose migration
3. For new features: read `03_FEATURE_BUILD_ORDER.md` for sequencing
4. For agent logic: read `02_AGENTS_AND_EDGE_CASES.md`
5. For ICommerceProvider work: this file + `02_AGENTS_AND_EDGE_CASES.md` Executor Agent section
6. Never bypass idempotency. Never bypass conversation state machine. Never call provider APIs directly from agents — always through ICommerceProvider.
7. PII redaction before Claude calls is mandatory.
8. Every new endpoint needs rate limiting.
9. Every new background job needs a dead-letter queue.
10. Every voice session must complete with a defined `outcome` — no silent state.
