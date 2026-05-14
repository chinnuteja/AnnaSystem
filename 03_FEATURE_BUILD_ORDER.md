# foodleaf — Feature Build Order (Sequenced, Not Time-Based)

> **When to load:** When deciding what to work on next, or when defining "done" for a feature. Pair with `00_PROJECT_CONTEXT.md`.
>
> **Philosophy:** No timelines. Each feature has clear acceptance criteria. Ship a feature only when the criteria pass — then move to the next. Don't bunch up half-features.

---

## Build Sequence Overview

```
PHASE A: Foundation (no user value yet, but everything depends on this)
   F1 — Repo + infra scaffolding
   F2 — Data model + migrations
   F3 — ICommerceProvider interface + mock Swiggy/ManualOps adapters
   F4 — Conversation state machine
   F5 — Idempotency + webhook plumbing

PHASE B: The Single-Item Voice Loop (the smallest end-to-end demo)
   F6 — Sarvam STT integration
   F7 — Message Parser Agent (text + voice, single item)
   F8 — SKU Mapper Agent (single item, exact match only)
   F9 — Confirmation Agent (text reply only)
   F10 — Sarvam TTS integration (voice reply)
   F11 — Executor Agent (mock provider, no real money)
   F12 — Acknowledge hack
   F12a — Discovery Agent (mock MCP, reasoned recommendations)

PHASE C: Real Money + Real Provider
   F13 — UPI Request system (Razorpay UPI Collect)
   F14 — Family Payer PWA: minimal onboarding + payer routing
   F15 — Provider adapters: Swiggy MCP + ManualOps fallback
   F15a — ONDCAdapter spike for MCP-independent coverage
   F16 — Multi-item voice ordering
   F17 — Order tracking + delivery confirmation

PHASE D: Care + Trust
   F18 — Care Monitor Agent (silence + anomaly)
   F19 — Family Payer PWA: care dashboard, voice session log
   F20 — Approval flow for high-value orders
   F21 — Mid-flow correction (amendment handling)
   F22 — Multi-member household support

PHASE E: Polish For Real Users
   F23 — All edge case handlers (EC-01 to EC-30)
   F24 — Vocabulary maps for Telugu regional variants
   F25 — Brand preference + partnership weighting infrastructure
   F26 — Catalog sync job (mirror Swiggy SKUs to pgvector)
   F27 — Soak testing + observability dashboards

PHASE F: Launch Readiness
   F28 — Onboarding flow (video-call assisted)
   F29 — Privacy + DPDP compliance
   F30 — Demo materials for Swiggy submission
```

---

## PHASE A — Foundation

### F1 — Repo + Infra Scaffolding

**Goal:** Get the basic project running locally and on Railway with Hello World.

**Build:**
- Monorepo structure as defined in `01_ARCHITECTURE.md` (apps/, packages/, infra/, scripts/)
- FastAPI app skeleton in `apps/api`
- Next.js 14 PWA skeleton in `apps/pwa`
- pyproject.toml with deps: fastapi, uvicorn, sqlalchemy, alembic, redis, httpx, anthropic, pydantic
- package.json for PWA: next, react, tailwindcss, shadcn-ui, lucide-react
- Railway project linked to repo, auto-deploys on push to main
- Vercel project linked for PWA
- Sentry integration (free tier) for both apps
- GitHub Actions CI: lint (ruff), type-check (mypy / tsc), unit tests

**Acceptance:**
- [ ] `foodleaf.in` resolves to PWA "Hello"
- [ ] `api.foodleaf.in/health` returns 200 with version + timestamp
- [ ] CI passes on a sample PR
- [ ] Sentry receives a test exception from both apps

---

### F2 — Data Model + Migrations

**Goal:** All Phase A-D tables exist and are migrate-able.

**Build:**
- Alembic setup with initial migration creating: families, users, family_payers, payment_requests, voice_sessions, canonical_skus, provider_sku_mappings, orders, care_signals, vocabulary_terms, brand_partnerships, acknowledgement_variants
- pgvector extension enabled
- TimescaleDB extension enabled, hypertables created for: payment_requests, voice_sessions, care_signals
- Seed data: 10 sample canonical_skus with embeddings (manually computed for testing), 50 vocabulary_terms covering common Telugu grocery words
- Database client wrapper in `packages/core/db.py` with connection pooling

**Acceptance:**
- [ ] `alembic upgrade head` creates all tables on a fresh Neon DB
- [ ] Seed data loaded via `scripts/seed.py`
- [ ] pgvector cosine similarity query works on canonical_skus
- [ ] TimescaleDB hypertable confirmed (check via psql `\d+ voice_sessions`)
- [ ] All models have Pydantic equivalents in `packages/core/models.py`

---

### F3 — ICommerceProvider Interface + Mock Provider Adapters

**Goal:** Provider abstraction is in place. SwiggyAdapter and ManualOpsAdapter exist but return hardcoded mock data.

**Build:**
- `packages/providers/interface.py` defining ICommerceProvider, CanonicalSKU, CartHandle, QuoteResult, OrderResult, OrderStatus, etc.
- `packages/providers/swiggy_adapter.py` implementing the interface, returning hardcoded mock data
- `packages/providers/manual_ops_adapter.py` implementing the interface by creating mock ops tasks
- `packages/providers/router.py` with simple "return swiggy" logic
- Mock data: 5 SKUs (Aashirvaad atta, Heritage milk, Dolo 650, MTR rava, Priya pickle) with realistic prices

**Acceptance:**
- [ ] Unit tests verify interface contract is implementable
- [ ] Test: `provider.search_skus("atta", "te-IN", location)` returns mock Aashirvaad result
- [ ] Test: full mock checkout flow returns OrderResult.success=true with synthetic order ID
- [ ] No `swiggy` references outside `swiggy_adapter.py` (enforced by import lint rule)

---

### F4 — Conversation State Machine

**Goal:** Per-user conversation state is tracked, transitions are atomic, TTLs work.

**Build:**
- `packages/core/conversation.py` with state machine class
- Redis-backed with Postgres mirror for durability
- State transitions defined as table; invalid transitions raise typed errors
- TTL handling: PARSING=2min, AWAITING_CONFIRMATION=30min, AWAITING_APPROVAL=60min, EXECUTING=2min
- Helper: `start_session()`, `transition(to_state)`, `current_state(ordering_user_id)`, `cancel_session()`

**Acceptance:**
- [ ] Unit tests for every legal transition
- [ ] Unit tests for every illegal transition (must raise)
- [ ] TTL expiry test: state auto-resets to IDLE after timeout
- [ ] Concurrent transition test: two simultaneous "transition to X" → exactly one wins, other gets stale error
- [ ] Mid-flow text or voice message (amendment) handled correctly

---

### F5 — Idempotency + Webhook Plumbing

**Goal:** WhatsApp webhooks land safely; duplicates are silently dropped; jobs are queued.

**Build:**
- Gupshup account setup, business number provisioned, webhook URL set to `api.foodleaf.in/webhook/whatsapp`
- Webhook signature verification middleware
- Idempotency middleware: SETNX check on `dedup:msg:{id}` with 24h TTL
- Redis queue `message_pipeline:incoming` (using Upstash); BullMQ or rq for the worker
- Job dispatcher: webhook → 200 → enqueue → return
- Worker stub that logs received jobs

**Acceptance:**
- [ ] `curl -X POST` with valid Gupshup signature lands a job
- [ ] Same message ID sent twice: only one job enqueued
- [ ] Invalid signature: 401 returned
- [ ] Worker picks up job and logs it within 1 second
- [ ] Webhook returns 200 in <300ms

---

## PHASE B — The Single-Item Voice Loop

End of Phase B, the demo is: Amma sends either Telugu text or a Telugu voice note "atta teesuko"; agent replies in the same mode with "Sare Amma, Aashirvaad atta rendu kilolu, naluguvanda iruvai rupayalu, confirm chey-yana?"; Amma says/types "avunu"; agent replies "Sare, order place ayyindi"; logged in DB. **No real money, mock provider.**

### F6 — Sarvam STT Integration

**Build:**
- `packages/agents/voice_parser.py` — initial scaffold
- Sarvam Saaras V3 API client in `packages/integrations/sarvam.py`
- Audio download from Gupshup CDN to local /tmp + R2 archive
- STT call returning transcription + confidence + detected language
- Fallback path: if Sarvam errors, call Gemini 2.5 Flash STT

**Acceptance:**
- [ ] Test with 5 real Telugu voice samples → transcriptions captured
- [ ] Test with code-mixed sample ("two kg Aashirvaad atta teesuko") → captured
- [ ] Test with very noisy audio → confidence drops below 0.7, agent flags
- [ ] Sarvam down (mocked): Gemini fallback fires, voice_session tagged `fallback_used=gemini`

---

### F7 — Message Parser Agent (Text + Voice, Single Item)

**Build:**
- Unified WhatsApp message normalizer: text messages pass through directly, voice messages go through STT
- Claude Haiku prompt for intent extraction (single-item-friendly)
- ParsedIntent Pydantic model includes `input_mode: "text" | "voice"`
- Persist interaction session row with parsed_intent
- Trigger downstream queue `message_pipeline:sku_resolve`

**Acceptance:**
- [ ] Text: "atta teesuko" → intent `{action: ORDER, input_mode: "text", items: [{text: "atta", quantity: null}]}`
- [ ] Voice: "atta teesuko" → same intent with `input_mode: "voice"`
- [ ] Text/voice: "Aashirvaad atta rendu kilolu" → `{action: ORDER, items: [{text: "atta", quantity: 2, unit: "kg", brand_hint: "aashirvaad"}]}`
- [ ] Text/voice: "elag unnaru" (chitchat) → `{action: CHITCHAT}`
- [ ] Text/voice: "naa order ekkada undi" → `{action: TRACK}`
- [ ] PII redacted in Claude calls (verified via logging)

---

### F8 — SKU Mapper Agent (Single Item, Exact Match)

**Build:**
- pgvector cosine similarity over canonical_skus
- Vocabulary map lookup for regional terms
- Family preference lookup (last-purchased)
- Returns top 1 candidate (no top-3 yet, no provider availability check yet — using mock provider)

**Acceptance:**
- [ ] "atta" → returns Aashirvaad atta (mock SKU)
- [ ] "godi pindi" (Telugu for atta) → also returns Aashirvaad atta
- [ ] "perugu" → returns curd SKU
- [ ] "biyyam" → returns rice SKU
- [ ] If no match: returns `unresolved_items` populated, candidates empty

---

### F9 — Confirmation Agent (Text Only)

**Build:**
- Claude Haiku prompt for Telugu confirmation script generation
- Send via Gupshup as text message (voice reply comes in F10)
- Set conversation state to `AWAITING_CONFIRMATION` with TTL
- Confirmation parser: detects "avunu" / "sare" / "vaddu" in incoming text or voice transcription

**Acceptance:**
- [ ] Single-item confirmation message generated in Telugu, grammatically correct, conversational
- [ ] State transitions to AWAITING_CONFIRMATION
- [ ] Text or voice reply "avunu" → triggers Executor; "vaddu" → cancels and returns to IDLE
- [ ] Unclear reply → re-asks once, then escalates "mee abbai ki cheppanu"

---

### F10 — Sarvam TTS Integration (Voice Reply)

**Build:**
- Sarvam Bulbul V3 API client
- Voice file upload to Cloudflare R2
- Send to WhatsApp via Gupshup as voice message
- Send parallel text message in Telugu script (for hard-of-hearing fallback)

**Acceptance:**
- [ ] Telugu confirmation text → natural-sounding voice file <500KB
- [ ] Voice plays cleanly on WhatsApp on iOS and Android
- [ ] Voice + text messages both arrive within same second
- [ ] If TTS fails: text-only fallback, voice_session tagged

---

### F11 — Executor Agent (Mock Provider)

**Build:**
- On positive confirmation, calls `provider.assemble_cart()` → `quote_cart()` → `execute_checkout()`
- Persists order row with mock provider_order_id
- Sends voice success message to ordering user
- Payment integration deferred to F13 (just mock the UPI Request result for now)

**Acceptance:**
- [ ] End-to-end demo: text in → confirmation → "avunu" → mock order placed → text success
- [ ] End-to-end demo: voice in → confirmation → "avunu" → mock order placed → voice success
- [ ] Order row persisted with status=confirmed
- [ ] Voice session has outcome=order_placed
- [ ] Conversation state returns to IDLE after 60-second cooldown

---

### F12 — Acknowledge Hack

**Build:**
- 8-12 acknowledge voice variants pre-recorded in Telugu, uploaded to R2, registered in `acknowledgement_variants` table
- Message Parser Agent, after intent extraction, schedules ack message in Redis with 1500ms delay
- Confirmation Agent cancels pending ack before sending its own message
- Variant selection logic: by context (long order, late hour, repeat order, generic)

**Acceptance:**
- [ ] When pipeline >1.5s: ack fires; user sees ack within 1.7-2s of voice send
- [ ] When pipeline <1.5s: ack cancelled; only final response sent (no double message)
- [ ] Variant rotation: 5 consecutive sessions show 5 different ack variants
- [ ] When pipeline hangs >12s: ack fired earlier, hard timeout sends graceful failure

---

### F12a — Discovery Agent (Mock MCP, Reasoned Recommendations)

**Goal:** Open-ended requests route to Discovery Agent and return 2-3 reasoned options before real provider integration.

**Build:**
- Extend ParsedIntent with `query_type: "specific_items" | "open_discovery"` and `discovery_context`
- `packages/agents/discovery.py` scaffold with mocked Food, Dineout, and Instamart results
- Ranking logic for taste match, active offers, distance/delivery time, rating, and brand partnerships as tiebreaker only
- Confirmation Agent support for DiscoveryResults, including "why this option" phrasing
- Follow-up handling: "first one", "more options", "no, just X" routes correctly

**Acceptance:**
- [ ] "Dinner ki manchi option chudu" routes to Discovery Agent, not SKU Mapper
- [ ] Response includes 2-3 options with clear reasoning, not a generic list
- [ ] "First one" converts selected discovery option into the normal confirmation/order path
- [ ] "More options" returns ranks 3-5 without losing context
- [ ] Discovery path respects the 1.5s acknowledge hack and 12s hard timeout

---

## PHASE C — Real Money + Real Provider

### F13 — UPI Request System (Razorpay UPI Collect)

**Build:**
- Razorpay merchant account setup
- `apps/api` endpoints: `POST /api/payment-requests`, `POST /api/payment-requests/{id}/retry`, `POST /webhook/razorpay`
- `family_payers` configuration: default payer, category routing, UPI handle, auto-approve threshold
- Razorpay UPI Collect integration: create collect request, wait for payer approval, process webhook
- `payment_requests` lifecycle: initiated → sent_to_payer → paid/rejected/expired/failed
- Executor integration: provider checkout fires only after `payment_request.status=paid`
- Refund path: if provider checkout fails after payment approval, immediately refund payer
- Reconciliation cron: hourly check for Razorpay payments/refunds without matching payment_request/order state
- Stripe Connect for NRI flow (parallel)

**Acceptance:**
- [ ] PWA: family payer can add UPI handle and set category routing in <2 minutes
- [ ] Test: standard UPI Request approved → payment_request paid → provider checkout proceeds
- [ ] Test: UPI Request rejected → no provider checkout, ordering user informed gracefully
- [ ] Test: UPI Request 90s timeout → order parked for retry, no provider checkout
- [ ] Test: Razorpay webhook duplicate → idempotent
- [ ] Test: provider failure after payment approval → refund initiated and payer notified
- [ ] Reconciliation finds and corrects any payment/order drift within 1 hour

---

### F14 — Family Payer PWA: Minimal Onboarding + Payer Routing

**Build:**
- PWA pages: signup (phone OTP via WhatsApp), add family member (phone, name, language, relationship), configure payer routing, view UPI request history
- WhatsApp OTP via Gupshup
- JWT auth, 24h sessions
- Magic link onboarding flow that family payer can share with another family member (new member then sends voice note to confirm activation)

**Acceptance:**
- [ ] Family payer can sign up + add a family member in <3 minutes
- [ ] New family member receives a Telugu voice welcome message after activation
- [ ] Payer routing and UPI handle setup work on mobile browser end-to-end
- [ ] New member's first voice note successfully creates a voice_session linked to family

---

### F15 — Provider Adapters: Swiggy MCP + ManualOps Fallback

**Build:**
- Replace mock SwiggyAdapter with real Swiggy MCP client if access is available (using their Python MCP SDK)
- Build `ManualOpsAdapter` implementing the same `ICommerceProvider` contract
- ManualOpsAdapter creates an internal ops task with cart, delivery address, payment_request_id, user language, and SLA
- Provider router priority: Swiggy MCP when healthy → ManualOpsAdapter for supported beta families
- Swiggy Builders Club API keys provisioned
- Real `search_products`, `update_cart`, `checkout`, `track_order`, `get_orders` calls
- Error handling: provider_unavailable, rate_limited, sku_unavailable, payment_failed, manual_ops_required
- Adapter idempotency: every checkout uses voice_session_id as client_request_id

**Acceptance:**
- [ ] Real order placed via Swiggy MCP if available → real Instamart delivery → physical confirmation
- [ ] If Swiggy MCP unavailable/rate-limited, same cart creates ManualOps task instead of failing silently
- [ ] ManualOps task can be marked placed/delivered/failed and updates the ordering user
- [ ] Failure modes tested: rate-limited returns clean error or routes to fallback gracefully
- [ ] Schema mismatch test (mock a slightly-changed response): adapter doesn't crash, returns typed error
- [ ] Idempotent retry test: same client_request_id called twice → only one order placed

---

### F15a — ONDCAdapter Spike for MCP-Independent Coverage

**Goal:** Prove foodleaf can work without any MCP server by speaking to ONDC-compatible commerce flows through the same provider interface.

**Build:**
- `packages/providers/ondc_adapter.py` implementing search/quote stubs first, checkout gated behind feature flag
- Map ONDC sellers/items into `canonical_skus` and `provider_sku_mappings`
- City/category allowlist so unreliable sellers never enter default routing
- Compare ONDC quote latency, price accuracy, cancellation behavior, and delivery reliability against Swiggy/manual ops

**Acceptance:**
- [ ] ONDCAdapter can search and quote at least one grocery/food category in a test city
- [ ] ONDC items map into the same SKU Mapper flow as Swiggy/manual catalog items
- [ ] Provider router can choose ONDC without changing agent code
- [ ] If ONDC checkout is disabled/unreliable, user is routed to ManualOps fallback or told gracefully
- [ ] Decision memo written: use ONDC in beta, keep as fallback only, or postpone

---

### F16 — Multi-Item Voice Ordering

**Build:**
- Message Parser Agent: handle multi-item intents from text and voice
- SKU Mapper Agent: parallel resolution of all items
- Confirmation Agent: list multiple items in natural Telugu
- Handle partial unavailability: "Amma, Aashirvaad atta vundi, Heritage milk vundi, kani Dolo lev — substitute teesukoni?"

**Acceptance:**
- [ ] "Atta rendu kilolu, paalu chinna packet, Dolo packet" → all three resolved correctly
- [ ] If one item unavailable: confirmation asks substitute, others proceed
- [ ] Latency for 5-item order: p95 <8s including ack

---

### F17 — Order Tracking + Delivery Confirmation

**Build:**
- Polling worker that calls `provider.track_order` every 60s for active orders
- Status updates to ordering user at key events: confirmed, out_for_delivery, delivered
- Post-delivery proactive voice 30 min after delivered: "Amma, anni items vacchaya?"
- Issue capture flow: if ordering user voice complains, mark issue, alert configured family payer/caregiver, file Swiggy refund

**Acceptance:**
- [ ] Real order tracked end-to-end → ordering user receives "out for delivery" voice 5 min before arrival
- [ ] Ordering user receives proactive check-in 30 min post-delivery
- [ ] Issue voice ("packet kharab vundi") → flagged correctly, care signal generated

---

## PHASE D — Care + Trust

### F18 — Care Monitor Agent (Silence + Anomaly)

**Build:**
- Cron job every 4 hours runs Care Monitor
- Detection rules: silence, duplicate_order, cognitive_pattern, upi_rejection_pattern, payer_balance_low, unusual_value, unusual_hour, delivery_failed
- Care signal persistence + dedup (48h window)
- Notification dispatch: WhatsApp utility template + PWA push for warn/urgent

**Acceptance:**
- [ ] Synthetic test: user stops ordering for 6 days → silence signal generated, configured family payer/caregiver notified
- [ ] Synthetic test: 4 identical orders in a day → cognitive_pattern urgent signal
- [ ] Anti-noise test: same signal type within 48h → only one notification

---

### F19 — Family Payer PWA: Care Dashboard

**Build:**
- Dashboard showing: family activity timeline, recent voice sessions (transcripts), unacknowledged care signals, weekly summary
- Voice session detail page with audio playback (auth-gated, second tap)
- Acknowledge signal flow
- Notification settings: info/warn/urgent levels per type

**Acceptance:**
- [ ] Dashboard loads in <2s on mobile
- [ ] All today's voice sessions visible with Telugu + English translation
- [ ] Care signals can be acknowledged with one tap
- [ ] Notification preferences honored: info-only filter suppresses warn/urgent

---

### F20 — Approval Flow for High-Value Orders

**Build:**
- Executor Agent: when `requires_child_approval=true`, send approval request via WhatsApp template + PWA push
- 60-min timeout with auto-cancel; if a UPI Request is pending, expire it cleanly
- Voice to ordering user: "Amma, [payer name] ki confirm cheyamani message pampincha"
- Approval/rejection PWA endpoints

**Acceptance:**
- [ ] Synthetic order >₹1500 → approval request to family payer within 5s
- [ ] Family payer taps approve in PWA → UPI Request flow proceeds, ordering user gets confirmation voice
- [ ] No approval in 60 min → auto-cancelled, ordering user informed gracefully
- [ ] Threshold configurable per family

---

### F21 — Mid-Flow Correction (Amendment Handling)

**Build:**
- Conversation state machine already supports this — wire up properly
- Message Parser Agent detects state=AWAITING_CONFIRMATION + new text/voice message → mark as amendment
- SKU Mapper merges previous + new context, re-resolves
- Confirmation Agent re-confirms

**Acceptance:**
- [ ] User says "atta rendu kilolu" → confirmation arrives → user says "no biyyam teesuko" → cart updated to rice, re-confirmed
- [ ] Multiple amendments (3+) handled without state corruption
- [ ] Final confirmation reflects ALL amendments

---

### F22 — Multi-Member Household Support

**Build:**
- Voice fingerprinting via Sarvam (or fallback: separate WhatsApp numbers per family member)
- Per-member brand prefs, dietary constraints
- Members share family payer routing rather than a prefunded balance
- Shared order history visible to family payer/caregiver grouped by ordering user

**Acceptance:**
- [ ] Mom's voice note routes to mom's profile (uses mom's brand prefs)
- [ ] Dad's voice note routes to dad's profile (different prefs)
- [ ] Correct payer receives the UPI Request regardless of which member ordered
- [ ] PWA shows orders tagged "by Amma" vs "by Nanna"

---

## PHASE E — Polish For Real Users

### F23 — All Edge Case Handlers (EC-01 to EC-30)

**Build:** Implement every handler from `02_AGENTS_AND_EDGE_CASES.md` that hasn't been built incidentally.

Specifically still needed at this phase:
- EC-02 (defunct brands)
- EC-04 (SKU unavailable substitute flow)
- EC-08 (wrong item delivered + refund)
- EC-10 (repeat-order pattern + soft order limit)
- EC-11 (COD bridging)
- EC-12 (viral signup from friend's number)
- EC-14 (festival long-list chunked confirmation)
- EC-19 (non-Swiggy city waitlist)
- EC-30 (new user verification on add)

**Acceptance:**
- [ ] Each EC has an automated test
- [ ] Manual run-through of each EC during beta verifies UX correctness
- [ ] No EC left unimplemented before launch

---

### F24 — Vocabulary Maps for Telugu Regional Variants

**Build:**
- Hand-curate vocabulary_terms for 200+ common grocery terms
- Coverage: Telangana Telugu, Coastal Andhra Telugu, Rayalaseema Telugu
- Per-region overrides where same word means different things (e.g. "perugu" vs "majjiga" disambiguation)
- Continuous learning: any voice session with low SKU confidence → flagged for vocabulary review

**Acceptance:**
- [ ] Telangana user: "kaaram" → chili powder
- [ ] Coastal Andhra user: "kaaram" → same → resolves correctly
- [ ] "majjiga" in Telangana → buttermilk; in coastal → could mean curd → ambiguous → confirmation asks
- [ ] At least 50 vocabulary items covered for top SKU categories

---

### F25 — Brand Preference + Partnership Weighting Infrastructure

**Build:**
- Brand preference setting in PWA per family member per category
- Brand_partnerships table with weight multipliers
- SKU Mapper re-rank logic includes weights
- Override rule: family preference always wins over partnership
- Partnership analytics: per-brand attribution dashboard for future sales conversations

**Acceptance:**
- [ ] Test: family with brand_pref=heritage_milk, partnership with nandini_milk → heritage wins
- [ ] Test: family with no preference, partnership with nandini → nandini ranked higher than equally-good alternatives
- [ ] Partnership analytics table populates as orders flow

---

### F26 — Catalog Sync Job (Mirror Swiggy SKUs)

**Build:**
- Cron job every 6 hours: for each active city, fetch top 500 SKUs per category from Swiggy Instamart
- Update canonical_skus + provider_sku_mappings + embeddings
- Detect discontinued SKUs, mark unavailable
- New SKU detection: alert for manual canonical assignment

**Acceptance:**
- [ ] Catalog sync runs without errors on schedule
- [ ] New SKU added on Swiggy → appears in our DB within 6 hours
- [ ] Discontinued SKU → marked unavailable, doesn't appear in search
- [ ] Embedding generation for 500 new SKUs completes in <10 min

---

### F27 — Soak Testing + Observability Dashboards

**Build:**
- Synthetic load generator: replay 1000 voice sessions over 24h
- Grafana dashboards: latency p50/p95/p99, error rates, queue depth, UPI approval/reconciliation drift
- Alerting: Slack/email on threshold breach
- Runbooks documented for top 5 incidents

**Acceptance:**
- [ ] 24h soak passes: no errors >0.5%, p95 latency <7s, no payment/order drift
- [ ] All dashboards render in <3s
- [ ] Test alert: synthetic incident triggers email within 60s
- [ ] Runbooks reviewed by you (founder) — you can recover from each scenario

---

## PHASE F — Launch Readiness

### F28 — Onboarding Flow (Video-Call Assisted)

**Build:**
- Founder-led onboarding: 15-min video call with family payer/caregiver + ordering user
- Family payer signs up on PWA, adds ordering user
- Ordering user saves foodleaf WhatsApp number, sends test text or voice note
- Founder verifies first 3 orders manually with both
- Self-serve onboarding doc for families who don't need video call

**Acceptance:**
- [ ] First 10 families onboarded with video call → 100% activation
- [ ] Self-serve onboarding doc tested with 3 NRI families → 80%+ activation
- [ ] Onboarding feedback collected, incorporated

---

### F29 — Privacy + DPDP Compliance

**Build:**
- Privacy policy + terms (drafted by you, reviewed by a real lawyer)
- Explicit consent capture from user on first text or voice interaction (user says/types "ha nenu agree" — recorded as consent)
- Voice retention controls: 90-day auto-delete, opt-in for longer
- Right-to-erasure: PWA endpoint to delete all family data
- Audit log of all data access

**Acceptance:**
- [ ] Privacy policy linked from PWA + WhatsApp first message
- [ ] Consent flow verified in onboarding
- [ ] Erasure endpoint deletes all family data within 24h
- [ ] Lawyer review complete (do not skip this)

---

### F30 — Demo Materials for Swiggy Submission

**Build:**
- 90-second demo video showing:
  - Amma sends Telugu text or voice note
  - Acknowledge fires within 2s
  - Confirmation voice in Telugu
  - "Avunu" → real Instamart order placed
  - Real delivery shown
  - Care signal demo
- Family payer PWA quick tour
- README on GitHub with architecture overview, the four-rail revenue model, traction numbers
- Email to builders@swiggy.in with: demo link, GitHub link, one-page pitch

**Acceptance:**
- [ ] Demo video <90s, watchable without sound, gripping
- [ ] GitHub README polished
- [ ] First email sent to Swiggy with concrete metrics (e.g. "10 paying families, ₹X/month GMV, 0 churn in 4 weeks")
- [ ] Follow-up plan: weekly metric snapshots to Swiggy until they respond

---

## What "Done" Looks Like for the MVP

After all 30 features ship, you should be able to demonstrate:

- 20+ real Telugu families using foodleaf daily/weekly
- ₹1-3 lakh/month GMV flowing through Swiggy
- Zero unrecovered UPI/payment incidents
- p95 latency under 7s
- Care signals catching real silences (with family verification that the catch was correct)
- 40%+ family-care premium conversion
- Working brand-partnership weighting (even if no partnership signed yet)
- Multi-member households working
- All 30 edge cases handled with tests

That is the artifact you walk into Swiggy with. That is the artifact that gets you a partnership conversation, not just a hire.

---

## Working With Cursor / Antigravity

When picking up a feature:

1. Read `00_PROJECT_CONTEXT.md` (always)
2. Read this file's section for that feature
3. Read the relevant section of `01_ARCHITECTURE.md` and `02_AGENTS_AND_EDGE_CASES.md`
4. Tell the AI assistant: "Build feature F[N] as defined in 03_FEATURE_BUILD_ORDER.md. Honor all acceptance criteria. Do not skip edge cases. Reference 01 for data model and 02 for agent behavior."
5. Verify each acceptance checkbox before considering the feature complete
6. Run the relevant tests before merging

Do not let the AI assistant start a feature before the previous one's acceptance criteria pass. Each feature is a contract.
