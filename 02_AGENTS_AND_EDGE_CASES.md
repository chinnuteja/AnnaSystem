# foodleaf — Agents & Edge Cases (Complete Behavior Spec)

> **When to load:** When implementing any of the 6 agents, the conversation state machine, or any edge case handler. Pair with `00_PROJECT_CONTEXT.md`.

---

## The Six Agents — Detailed Specs

Each agent is a Python module in `/packages/agents/`. Each has a single responsibility, well-defined inputs, well-defined outputs, and explicit failure modes.

---

### Agent 1: Message Parser Agent

**Module:** `packages/agents/message_parser.py`
**Triggered by:** Redis queue `message_pipeline:incoming`
**Input:** `{family_id, ordering_user_id, whatsapp_message_id, input_mode: "text" | "voice", text_body?, audio_url?}`
**Output:** Persists interaction row with `parsed_intent`, advances state to `PARSING` then to next stage

#### Pipeline

1. **Idempotency check** (Redis SETNX) — if already processed, skip
2. **Normalize input**
   - Text message: use `text_body` directly, detect language, skip STT
   - Voice message: download audio from Gupshup CDN to local temp + R2 archive
3. **For voice only: pre-process audio** — RNNoise denoise, AGC normalize (Sarvam may handle internally; verify)
4. **For voice only: send to Sarvam Saaras V3** — streaming if available, else batch
5. **Build `normalized_text`** from text body or transcription, with confidence + detected language when available
6. **Send normalized_text to Claude Haiku** with intent extraction prompt (see prompt template below)
7. **Receive structured ParsedIntent**:
   ```json
   {
     "action": "ORDER" | "DISCOVER" | "CANCEL" | "TRACK" | "AMEND" | "CHITCHAT" | "UNCLEAR",
     "input_mode": "text" | "voice",
     "query_type": "specific_items" | "open_discovery",
     "discovery_context": {
       "meal_type": "dinner" | "lunch" | "breakfast" | "snack" | null,
       "category_hint": "biryani" | "chinese" | "groceries" | "dine_in" | null,
       "exclusions": ["had biryani this week"],
       "budget_hint_inr": 500,
       "occasion": null
     },
     "items": [
       {"text": "atta", "quantity": 2, "unit": "kg", "brand_hint": null}
     ],
     "urgency": "normal" | "urgent",
     "delivery_pref": null | "now" | "scheduled",
     "language_detected": "te-IN",
     "confidence": 0.87,
     "raw_transcription": "<original Sarvam output>"
   }
   ```

   When `query_type=open_discovery`, items array is empty and routing goes to Discovery Agent. When `query_type=specific_items`, routing goes to SKU Mapper Agent.
8. **Persist** to interaction session row
9. **Route** to Discovery Agent via `message_pipeline:discover` when `query_type=open_discovery`, or SKU Mapper Agent via `message_pipeline:sku_resolve` when `query_type=specific_items`

#### Confidence handling

Text input has no STT confidence; apply parser confidence only. Voice input uses both STT and parser confidence.

| Sarvam confidence | Behavior |
|---|---|
| ≥0.85 | Proceed normally |
| 0.70-0.84 | Proceed but always re-confirm before checkout |
| 0.50-0.69 | Send "Sorry Amma, ardham kavadam ledu, malli cheppandi specific ga" — mark voice_session `outcome=clarification_requested` |
| <0.50 | Same as above, plus log low-confidence to monitoring |

#### Prompt for intent extraction (Claude Haiku)

```
You are a parser for an Indian family WhatsApp ordering app. The user is a family member
who sent either a text message or a voice note that was transcribed.

The message may contain code-mixed Telugu-English-Hindi. Your job: extract structured
intent ONLY. Do NOT add information that wasn't said. Do NOT correct grammar.

Output strict JSON matching this schema: { ... }

Items: extract every distinct item the user mentions. If quantity is missing, use null.
If unit is missing, use null. If they say "Dolo" infer brand_hint="dolo_650" only if that's
the most common Dolo product. If they mention multiple competing items in one phrase,
extract them separately.

Action types:
- ORDER: user wants to buy something
- CANCEL: user wants to cancel a recent order
- TRACK: user is asking about delivery status
- AMEND: user is correcting/changing a previous request (e.g. "actually no biryani, just rice")
- CHITCHAT: user is just chatting, no commercial intent
- UNCLEAR: cannot determine

Input mode: "{INPUT_MODE}"
Message text: "{NORMALIZED_TEXT}"
Confidence: {CONFIDENCE}
User's typical order history (last 10 items): {RECENT_ITEMS}
User's set brand preferences: {BRAND_PREFS}

Output JSON only.
```

#### Failure handling

- **Audio download fails:** retry 3x, then mark `outcome=audio_unavailable`, send "Amma, voice note ki access kavadam ledu, text lo pampandi leda voice malli pampandi"
- **Text message is ambiguous:** ask one clarifying question in text first; offer voice fallback if the user seems stuck
- **Sarvam API error:** fall back to Gemini 2.5 Flash. If both fail: "Amma, technical problem, malli try chesthana?"
- **Claude returns invalid JSON:** retry once with stricter prompt; if still bad, treat as `action=UNCLEAR`
- **Pipeline timeout (>4s in this stage):** abort, send acknowledge message buffer

---

### Agent 2: Discovery Agent

**Module:** `packages/agents/discovery.py`
**Triggered by:** Message Parser when `query_type=open_discovery`
**Input:** `{voice_session_id}` (which has parsed_intent with discovery_context)
**Output:** Persists `discovery_results` JSON in voice_session, advances to Confirmation Agent

This is the **differentiator agent**. It's what makes foodleaf radically better than Swiggy/Zomato native search. Treat its design as carefully as the Executor.

#### Pipeline

1. **Determine target provider adapters to query** based on discovery_context:
   - "dinner" / "lunch" / "snacks" → food delivery adapters + dineout/reservation adapters
   - "groceries" / "raw materials" / specific item categories → grocery adapters
   - "place to eat out" / "restaurant for tomorrow" → dineout/reservation adapters primary, food delivery secondary
   - Ambiguous → all available relevant adapters in parallel
2. **Build context vector** for personalization:
   - User's last 30 days of orders (cuisines, price bands, brands, frequency)
   - User's stated preferences (vegetarian, dietary, allergens)
   - Time-of-day, day-of-week, weather (rainy → comfort food bias; hot → light/cold bias)
   - Location and 3km/5km radius
   - User's exclusions from this query ("not biryani this week")
3. **Parallel provider search** (this is the speed-critical part):
   - Swiggy MCP adapters when available: restaurant/product/menu search
   - ONDCAdapter when available: seller/item search + quote probes
   - DirectProviderAdapter where integrated: provider-native search
   - ManualOpsAdapter fallback: search curated/manual catalog and create an ops task if the user chooses it
4. **Gather offers** — for each candidate, surface any active deals, BOGO, %-off, free delivery, Swiggy One benefits
5. **Re-rank** with weights:
   - Match to user's past taste: ×2.5
   - Active offer/deal value: ×1.5
   - Distance/delivery time fit: ×1.2
   - Rating threshold: ×1.0 (zero below 3.8 stars)
   - Brand partnership weight: ×1.0-1.3 (only as tiebreaker, never overrides taste match)
6. **Select top 2-3 options with reasoning** — not a list of 10
7. **Compose conversational response** via Claude Haiku — must include the WHY, not just the WHAT

#### Output structure

```json
{
  "query_type": "open_discovery",
  "options": [
    {
      "rank": 1,
      "type": "food_delivery",
      "provider": "swiggy_food",
      "restaurant_name": "Sai Punjabi Dhaba",
      "highlight_dishes": ["Butter Chicken", "Garlic Naan"],
      "estimated_total_inr": 450,
      "delivery_min_max": [25, 35],
      "active_offer": "30% off this weekend",
      "reasoning_for_user": "your favourite cuisine, this weekend offer, 8 min away"
    },
    {
      "rank": 2,
      "type": "dineout",
      "provider": "swiggy_dineout",
      "restaurant_name": "Cream Centre",
      "deal": "Veg buffet ₹699 unlimited",
      "distance_km": 8,
      "available_slots_today": ["7:30 PM", "8:00 PM", "9:00 PM"],
      "reasoning_for_user": "dine-in option, weekend special, 8 minutes drive"
    }
  ],
  "user_context_used": {
    "exclusions": ["biryani"],
    "personalization_signals": ["likes North Indian", "rainy weather", "Friday night"]
  }
}
```

#### Confirmation pattern (handed to Confirmation Agent)

> "Sare, mee taste ki Sai Punjabi Dhaba try cheyandi. Butter chicken naan combo ₹450 lo, weekend offer 30% off, 30 nimishala lo vasthundi. Leda Cream Centre lo veg buffet ₹699, dine-in cheyali antey table book chey-yana? Order chey-yana, ye di?"

User can pick: "first one" / "Sai Punjabi" / "buffet table book chey" / "leda inkemina chudu" (show me more)

If user says "more options," agent returns ranks 3-5. If user says "no, just biryani lol" — re-route as ORDER intent.

#### Edge cases for Discovery

- **No good matches found** (all options below quality threshold): "Sorry, mee taste ki match ayye options ledu ee time lo. [List 2 widely-popular options with caveat]"
- **All options have long delivery time** (>50 min): proactively flag "delivery konchem late avtundi today, dineout options chudali?"
- **User has no order history** (cold start): bias toward popular + highly-rated + active-offer; ask one clarifying question voice "Spicy istharaa, leda mild ga?"
- **Discovery Agent itself slow** (provider calls aggregate >4s): acknowledge hack fires, but Discovery's confirmation message includes "konchem time padindi, mee kosam best options chustunna"
- **All live providers unavailable:** return manual fallback options only if the family is in the supported beta ops area; otherwise explain that ordering is temporarily unavailable and offer to retry later

#### Why this agent is the moat

- Swiggy can build voice ordering. They already are.
- Swiggy *can* technically build cross-provider discovery, but **they won't optimize it for users** because their search ranking is monetized via paid placements. A neutral third-party agent can give the user the genuinely best answer (with brand partnerships only as tiebreaker, never overriding match quality). This is a real, defensible product wedge.

---

### Agent 3: SKU Mapper Agent

**Module:** `packages/agents/sku_mapper.py`
**Triggered by:** Redis queue `message_pipeline:sku_resolve`
**Input:** `{voice_session_id}`
**Output:** Persists `resolved_cart` JSON in voice_session, advances to Confirmation Agent

#### Pipeline

For each item in `parsed_intent.items`:

1. **Cache check** — Redis key `sku_cache:{family_id}:{normalized_item_text}` → if hit and <7 days old, use it
2. **Family preference check** — has this family bought this item before? If yes, retrieve top brand+SKU from order history
3. **pgvector search** — embed `item_text + region` query, cosine similarity vs `canonical_skus.embedding`, top 5
4. **Vocabulary map check** — look up `vocabulary_terms` for regional Telugu word → category
5. **Provider availability check** — for top 5 candidates, call `ICommerceProvider.check_availability` in parallel
6. **Re-rank** with weights:
   - Previous purchase preference: ×2.0 (strong)
   - Brand preference set in family settings: ×1.8
   - Brand partnership weight: ×1.0-1.5 (only when no prior preference)
   - Price band fit (within typical band for that family): ×1.2
   - Provider availability & freshness: ×1.0 (zero if unavailable)
7. **Select top 1-3 candidates** with score above threshold

After all items processed:

8. **Compute estimated cart value** (sum of top candidate prices)
9. **Compare to family's normal cart value** — if >2x normal → flag `requires_child_approval=true`
10. **Persist ResolvedCart** structure:
    ```json
    {
      "items": [
        {
          "intent_text": "atta",
          "candidates": [
            {"canonical_sku_id": "...", "score": 0.94, "display_name": "Aashirvaad Select Atta 2kg", "price_inr": 280, "provider_sku_id": "INST-12345"},
            {...},
          ],
          "selected_index": 0,
          "quantity": 2
        }
      ],
      "estimated_subtotal_inr": 740,
      "estimated_delivery_inr": 0,
      "estimated_total_inr": 740,
      "requires_child_approval": false,
      "unresolved_items": [],
      "warnings": []
    }
    ```

#### Edge cases handled here

- **Item not found at all:** add to `unresolved_items`. Confirmation agent will say "Amma, [item] maa daggara ledu, ila vundi, teesuko-na?" with closest substitute
- **Multiple equally-good matches:** include top 3, let confirmation agent ask "Amma, ee rendu lo ye di? Heritage milk laga, leda Nandini?"
- **Defunct brand mention** (e.g. "Mother Dairy" used generically): vocabulary map flags this, agent uses last-purchased brand in that category
- **Quantity ambiguous:** use family's typical quantity for this item, or default pack size from canonical_sku
- **Price spike** (selected SKU price >1.4x typical band): add warning, surface to confirmation agent for explicit price call-out

#### Brand partnership conflict rule

If family has a `brand_preferences[category] = "heritage"` AND we have a partnership with Nandini:
- **Heritage wins, always.** No partnership boost.
- Log this as `partnership_overridden_by_preference` (this is a contractual assurance to brands)

If family has no preference set AND user said only the category ("paalu"):
- Then partnership weight applies as a tiebreaker between similarly-scored candidates

---

### Agent 4: Confirmation Agent

**Module:** `packages/agents/confirmation.py`
**Triggered by:** SKU Mapper completion
**Input:** `{voice_session_id}`
**Output:** Sends voice + text WhatsApp message; sets state to `AWAITING_CONFIRMATION`; sets timeout

#### Pipeline

1. **Build confirmation payload** from `resolved_cart`
2. **Generate Telugu confirmation script** via Claude Haiku:
   - Read items naturally: "Amma, Aashirvaad atta rendu kilolu, Dolo chinna packet, Heritage paalu chinna packet"
   - Mention total: "anni kalipi nooru padi rupayalu"
   - Mention delivery time if known
   - Mention any warnings: "biyyam price ee roju konchem ekkuvundi"
   - Ask for confirmation: "confirm chey-yana?"
3. **Generate Telugu audio** via Sarvam Bulbul V3
4. **Send to WhatsApp** — voice message + parallel text message (in Telugu script for hard-of-hearing fallback)
5. **Update conversation state** in Redis: `state=AWAITING_CONFIRMATION, pending_cart=<resolved_cart>, expires_at=now+30min`
6. **Schedule timeout** — if no confirmation in 30 minutes, send gentle: "Amma, order place cheyyala? Confirm cheppandi" (one ping only — never multiple)

#### Acknowledge Hack

Before this agent runs, if the upstream pipeline (Message Parser + SKU Mapper) is taking >1.5s:

- Message Parser Agent, immediately after intent extraction, schedules an acknowledgement message in Redis with `delay=1500ms`
- A small worker watches this; if not cancelled, fires a pre-recorded "Sare Amma, chustunnanu..." voice clip via Gupshup
- Confirmation Agent, when it starts running, **cancels** the pending ack if it hasn't fired yet
- 8-12 variants of the ack message rotated (selected based on context: long order, late hour, repeat order, etc.)
- Variants stored as static files in R2, references in `acknowledgement_variants` table

#### Confirmation patterns

**Single-item, high confidence, repeat order:**
> "Sare Amma, mamulu Aashirvaad atta rendu kilolu, naluguvanda iruvai rupayalu. Confirm chey-yana?"

**Multi-item, mixed:**
> "Sare Amma, atta rendu kilolu, paalu chinna packet, Dolo packet — anni kalipi mooduvanda nelabai rupayalu, gantalo vasthundi. Confirm chey-yana?"

**Ambiguous item:**
> "Amma, paalu lo ee rendu vunnayi: Heritage chinna packet leda Nandini chinna packet. Yeti teesukoni?"

**Price warning:**
> "Amma, biyyam ee roju konchem ekkuva price undi — kilo rupayalu yenabai. Inkocchadu chesukoni?"

**Item unavailable:**
> "Amma, mee favorite Aashirvaad atta ee time lo lev. Pillsbury atta vundi, same kilo lo, teesukoni?"

#### Confirmation parsing

When the ordering user's reply text or voice note arrives, this agent (or a helper) parses for confirmation:

- Positive: "avunu" / "sare" / "ok" / "place chey" / "yes" / "ha" → trigger Executor Agent
- Negative: "vaddu" / "no" / "cancel" / "vaddhu" → mark cancelled, reset state to `IDLE`
- Amendment: "actually no Aashirvaad, Pillsbury teesuko" / "atta vaddu, biyyam tho replace chey" → re-trigger SKU Mapper with combined context
- Unclear: re-send simplified confirmation, max 2 retries; then escalate "Amma, mee abbai ki cheppanu, please?"

---

### Agent 5: Executor Agent

**Module:** `packages/agents/executor.py`
**Triggered by:** Positive confirmation in Confirmation Agent
**Input:** `{voice_session_id}` (which has resolved_cart)
**Output:** Order placed via ICommerceProvider after UPI payment confirmed; updates `orders` table; notifies family

#### Pipeline

1. **Re-validate state** — Redis convo state must be `AWAITING_CONFIRMATION` and not expired
2. **Resolve payer** — look up `family_payers` for this family + cart category. Determines whose UPI gets charged.
3. **Determine approval mode:**
   - If `cart.total <= payer.auto_approve_threshold_inr` AND `payer.trust_started_at + 30 days < now()` → auto-approve mode (silent UPI charge via UPI Autopay if pre-mandated, else still requires tap)
   - If `cart.total > soft_approval_threshold` (default ₹1,500) → explicit approval flow with PWA notification first
   - Otherwise → standard UPI Request flow (tap to approve in payer's UPI app)
4. **Create payment_request row** with `status=initiated`, unique constraint on voice_session_id (prevents duplicates)
5. **Initiate Razorpay UPI Collect** to payer's UPI handle for `cart.estimated_total`. Razorpay returns request_id, sends notification to payer's UPI app. Mark `status=sent_to_payer`.
6. **Voice to ordering user:** "Sare, [payer name] ki UPI request pampancha, approve aithe order place avtundi"
7. **Wait up to 90 seconds** for Razorpay webhook with payment status:
   - **Approved/paid:** mark `payment_request.status=paid` → proceed to step 8
   - **Rejected:** mark `status=rejected`, voice "Sorry, payment approve avvaledhu. Malli try chesthana?"; reset state to IDLE
   - **Timeout (90s):** mark `status=expired`, voice "Payment time out ayyindi, [payer name] ki call chesi try chesthara?"; order parked for 6h, retry button on PWA
8. **Call provider** — `provider.assemble_cart()` → `provider.quote_cart()` → `provider.execute_checkout()` with payment_request_id as idempotency key
9. **On provider success:**
   - Persist `order` row with `status=confirmed`, link `payment_request_id`
   - Send ordering user voice: "Sare, order place ayyindi, [time] lo vasthundi"
   - Send PWA notification + WhatsApp utility template to payer with order summary
   - Reset conversation state to `IDLE`
10. **On provider failure (after payment was approved):**
    - **Critical: refund the payment.** Initiate Razorpay refund to payer's UPI immediately
    - Persist failed order row with `failure_reason`
    - Send ordering user: "Sorry, [reason]. Payment refund chesindi, [payer] ki return avtundi 1-2 hours lo"
    - Send `provider_failure` care signal to family payer

#### Critical correctness invariants

- **Provider checkout NEVER fires before payment_request.status=paid.** Hard rule.
- **Refund always fires if provider fails after payment approved.** No exceptions. Reconciliation job verifies hourly.
- **Idempotency:** `voice_session_id` is unique key for both `payment_requests` and provider `client_request_id`. Retries are safe.
- **No silent failures.** Every voice session ends with a defined `outcome`.
- **Payer can never be charged twice for same voice_session_id.** DB unique constraint enforces this.

#### Provider routing (future-proofing)

In MVP, hardcoded to SwiggyAdapter. The hook is here:

```python
provider = provider_router.choose(family, cart, policy="default")
result = await provider.execute_checkout(cart, payment_ref, customer)
```

`provider_router.choose()` in MVP just returns `SwiggyAdapter`. In year 2, this becomes a real policy engine.

---

### Agent 6: Care Monitor Agent

**Module:** `packages/agents/care_monitor.py`
**Triggered by:** Cron every 4 hours
**Input:** None (scans all active families)
**Output:** Inserts `care_signals` rows; sends WhatsApp to configured family payer/caregiver for severity ≥ warn

#### Detection rules

For each active ordering user:

**Silence anomaly:**
- Compute `expected_order_interval_days` = rolling 30-day average gap between orders
- If `days_since_last_order > 2 × expected_order_interval`, flag `severity=warn`
- If `days_since_last_order > 4 × expected_order_interval`, flag `severity=urgent`
- Skip if family is in vacation mode (settable from PWA)

**Duplicate / cognitive pattern:**
- Look at last 7 days of orders
- If 3+ orders contain same SKU within 24-hour windows on multiple days → flag `cognitive_pattern severity=warn`
- If 2+ orders within 4-hour cooldown despite Confirmation Agent's check → flag `severity=urgent` (means a user is bypassing the normal flow somehow)

**UPI rejection pattern:**
- If 3+ UPI requests rejected by payer in 24h → flag `upi_rejection_pattern severity=warn` to family (could indicate payer bandwidth issue or family conflict)
- If payer's UPI returns "insufficient balance" error → flag `payer_balance_low severity=info`

**Unusual value:**
- If any order in last 24h was >2.5× rolling 30-day average cart value → already would have triggered child approval, but if approved, log `unusual_value info`

**Unusual hour:**
- Orders placed between 1 AM and 5 AM IST — log `unusual_hour info` (could indicate confusion or insomnia)

**Delivery failed:**
- Order with `status=failed` or `delivery_failed` → flag `severity=warn`

#### Care signal delivery

- `info` signals: appear in PWA dashboard only, no push
- `warn` signals: WhatsApp utility template to configured family payer/caregiver + PWA push
- `urgent` signals: WhatsApp utility template + PWA push + email to configured family payer/caregiver

#### Anti-noise rules

- Same signal type for same user within 48 hours: collapse into one
- Family payer/caregiver can configure: "only warn me about urgent" → suppress info/warn

---

## Conversation State Machine

State lives in Redis (`convo:{ordering_user_id}`), mirrored to Postgres on transition.

```
States:
  IDLE
  PARSING               (voice received, intent being extracted)
  AWAITING_CONFIRMATION (cart resolved, waiting for "avunu")
  AWAITING_APPROVAL     (child approval requested)
  EXECUTING             (provider call in flight)
  COMPLETE              (terminal — auto-resets to IDLE after 1 min)

Transitions:
  IDLE → PARSING        : WhatsApp text or voice message received
  PARSING → AWAITING_CONFIRMATION : intent + cart resolved successfully
  PARSING → IDLE        : action=CHITCHAT or UNCLEAR or pipeline failed
  AWAITING_CONFIRMATION → PARSING : amendment received (new text or voice message)
  AWAITING_CONFIRMATION → AWAITING_APPROVAL : confirmed but cart>threshold
  AWAITING_CONFIRMATION → EXECUTING : confirmed within threshold
  AWAITING_CONFIRMATION → IDLE      : cancelled or timeout
  AWAITING_APPROVAL → EXECUTING : child approved
  AWAITING_APPROVAL → IDLE      : child rejected or timeout
  EXECUTING → COMPLETE  : provider responded (success or failure)
  COMPLETE → IDLE       : 60-second cooldown

Special rules:
  - Any text or voice message while EXECUTING is queued, processed after EXECUTING ends
  - Any text or voice message while AWAITING_CONFIRMATION cancels in-flight, treats as amendment
  - States have TTL: PARSING=2min, AWAITING_CONFIRMATION=30min, AWAITING_APPROVAL=60min, EXECUTING=2min
  - On TTL expiry, state → IDLE with care signal logged
```

---

## Edge Case Handlers — Complete Catalog

Each is a documented behavior. Many have unit tests.

### EC-01: Audio quality is terrible

**Detection:** Sarvam confidence <50%
**Handler:** Reply "Sorry Amma, naaku vinabadaledu. Konchem dagga ra cheppandi malli?" (Sorry, I couldn't hear, please come closer and say again)
**State:** Stay in PARSING for 2 min waiting for retry; if no retry, transition to IDLE with care signal `info`

### EC-02: Defunct brand names

**Detection:** Vocabulary map flags term as `defunct` or `generic` (e.g. "Mother Dairy", "Surf")
**Handler:** Use family's last-purchased brand in that category. If no history, use most popular brand in their region.
**Confirmation always says the actual brand:** "Amma, paalu antunnaru kada — Heritage chinna packet teesuko-na?"

### EC-03: Missing quantity

**Detection:** ParsedIntent has `quantity=null` for an item
**Handler:** Use last-purchased quantity for that family. If no history, use canonical default pack size.
**Confirmation explicitly states inferred quantity:** "Amma, mamulu rendu kilolu kada, atta rendu kilolu teesthana?"

### EC-04: SKU doesn't exist on Instamart in their location

**Detection:** Provider check_availability returns no matches
**Handler:** Find closest substitute in same category. Surface clearly: "Amma, [requested] maa daggara ledu. Bahusha [substitute] vundi, ade kavalantey teesukoni?"
**Log:** Add to `missed_skus` table for product team analysis

### EC-05: Confused over-ordering

**Detection:** Order value >2× rolling 30-day avg, OR exact same items as yesterday's order, OR 4+ orders in same day
**Handler:** Set `requires_explicit_approval=true`. Voice to ordering user: "Ee order konchem pedda ga undi, [payer name] ki kuda confirm cheyamani cheppanu — okka second"
**Family payer gets:** WhatsApp template "[User] is ordering ₹X with these items: [list]. Approve / Modify / Cancel" + PWA push

### EC-06: Payer rejects or doesn't approve UPI Request

**Detection:** Razorpay webhook indicates rejection, or 90s timeout passes with no response
**Handler:**
1. **If rejected:** Voice to ordering user: "Sorry, payment approve avvaledhu. Malli try chesthana, leda inkemina kavalantey?"
   - Mark voice session `outcome=payment_rejected`
   - State resets to IDLE
2. **If timeout (90s):** Voice to ordering user: "Payment time out ayyindi. [Payer name] busy ga unnaru maybe. Order parked chesinanu, malli try cheyali antey 'try again' cheppandi"
   - Order parked for 6 hours, retry button available on PWA + voice
   - User can change payer if family has multiple configured
3. **If 3+ rejections in 24h from same payer:** Care signal `upi_rejection_pattern warn` to family — could indicate payer's UPI app issue or genuine conflict; family payer config may need review

### EC-06b: Payer's UPI returns "insufficient balance"

**Detection:** Razorpay webhook with specific failure code
**Handler:**
1. Voice to ordering user: "[Payer name] account lo balance lev anta. Vere person try cheyali antey, leda apply for now?"
2. If family has alternate payer configured for this category, offer fallback: "Bahusha [alt payer] try cheyani?"
3. Order parked, payer notified via PWA "Insufficient balance for ₹X order — top up your UPI account?"

### EC-07: Delivery rider can't find the address

**Detection:** Order tracking shows `out_for_delivery` for >40 minutes; rider attempts contact and fails
**Handler:**
1. First 3 orders to a new family: founder/CS team calls rider directly to guide. Manual escalation queue in PWA.
2. After 3 successful deliveries: address is "calibrated" — pin location locked, landmark instructions saved.
3. If failure persists: care signal `delivery_failed urgent` to configured family payer/caregiver + voice to ordering user: "Amma, delivery person ki address dorakatledu, [family payer name] ki call cheyamani cheppanu?"

### EC-08: Wrong item delivered

**Detection:** Manual flagging via voice ("Amma chinna problem vundi") OR proactive check
**Handler:**
- After every delivery, send proactive voice 30 min later: "Amma, packet open chesi check chesara? Anni items vunnaya?"
- If ordering user reports issue: capture in voice session with type=ISSUE, flag care signal, automated Swiggy refund flow via support API or manual ticket
- Configured family payer/caregiver notified

### EC-09: Multi-member household

**Detection:** Family has 2+ users enrolled on the same WhatsApp number (or different numbers under same family_id)
**Handler:**
- Voice fingerprinting via Sarvam (each user's voice profile saved at onboarding)
- Each voice note routed to correct user_id
- Each user has their own brand prefs / dietary constraints
- Payment routes to family payer (could be same person, different person, or category-based routing)
- If voice fingerprint confidence <0.7: agent asks "Sare, idi [name1] aa, [name2] aa?" or uses phone-number disambiguation if separate numbers

### EC-10: Repeat-order pattern (potential cognitive concern)

**Detection:** 3+ identical orders within same day OR same item ordered 4+ times within 8 hours
**Handler:**
- Hard cooldown: voice agent says "Idi pavu gantalo kritham order chesaru — malli kavalena? Konfirm cheyandi"
- Care signal `cognitive_pattern urgent` to family payer after 3-day pattern (only if family has opted into care features)
- PWA shows pattern visualization + suggests setting daily order limit
- Optional family setting: "soft order limit" — max 2 orders/day, configurable per user

### EC-11: Cash-on-delivery insistence

**Detection:** User says variants of "naa daggara cash isthanu" or refuses online payment in onboarding
**Handler:**
- Where Swiggy supports COD: enable it for that family
- Order placed with COD; family payer pays the COD amount via UPI Request to the user (or directly settles with the rider via the user's phone if family has set up COD-bridge)
- Alternate flow: "phantom COD" — order is actually paid via UPI Request to family payer in background, but user is told it's COD. They pay nothing to rider; rider is pre-marked as paid in Swiggy.
- This bridges a real psychological trust gap; document as standard configurable family setting

### EC-12: User shares number with friends

**Detection:** A new phone number sends a voice note to our WhatsApp Business number
**Handler:**
- Agent replies in Telugu: "Namaskaram! foodleaf ki swagatham. Mee perulu, family member ni intro cheyandi please?"
- Capture interest in onboarding queue
- Send SMS invite link to family-onboarding flow
- This is **viral growth** — track conversion of friend-of-friend signups

### EC-13: Multilingual mid-sentence

**Example:** "Aashirvaad atta two kg, milk also chinna packet"
**Handler:** Sarvam Saaras handles code-mix natively. Claude parser explicitly told to handle code-mix. Confirmation always replies in user's primary language (set per user, defaults to Telugu).

### EC-14: Festival long-list ordering

**Detection:** Voice note >30 sec OR ParsedIntent has 8+ items
**Handler:**
- Confirmation Agent uses chunked confirmation:
  - "Ippati daka ee items pettanu: [list of 5]. Inkemaina add cheyalena?"
  - User replies "yes still need [more]" → re-trigger parser with combined context
  - When user says "antey" / "all done" → final consolidated confirmation
- Festival-aware boost: if Indian calendar shows major festival in 7 days, agent adds gentle suggestion: "[Festival] vasthundi kada — modak / payasam materials add cheyalena?"

### EC-15: Family member override attempts

**Scenario:** Family payer sees user ordered ₹50 of biscuits, wants to cancel after the fact
**Handler:**
- **No.** Family payer cannot cancel another user's confirmed orders. Pre-payment via UPI Request structure already gave them a chance to reject.
- They CAN suggest replacements via PWA → voice agent surfaces gently: "[Payer name] healthier biscuits suggest chesaru, want to try next time?"
- User decides. **User agency is sacred for confirmed orders.**
- Document this in PWA copy clearly so family understands the boundary

### EC-16: User's phone dead/lost/stolen

**Detection:** Family member reports via PWA "[user's name] phone unavailable, place order on their behalf"
**Handler:**
- Family payer PWA gets a temporary "order on behalf" mode
- Voice agent confirms with user via landline IVR (year 2 feature; for MVP, family member writes a text order via PWA, we deliver, no voice confirmation)
- Restricted: only essential items (milk, medicines) in this mode; no >₹1000 orders

### EC-17: Brand partnership conflict

**Already documented** in SKU Mapper section. Existing preference always wins.

### EC-18: Commerce provider down or rate-limited

**Detection:** Adapter exception with provider_unavailable/rate_limited code from Swiggy MCP, ONDC, direct API, or manual ops capacity
**Handler:**
- Provider router tries the next eligible adapter before failing
- If ManualOpsAdapter is available, create ops task and tell user it may take longer
- If all adapters fail, reply to ordering user: "Amma, ee time lo provider problem undi, koddi sepatlo malli try chestha"
- Mark session `outcome=failed`, `failure_reason=provider_unavailable` only after all fallback adapters fail
- Auto-retry every 15 min for up to 2 hours
- If recovered within window: message ordering user "Amma, ippudu try cheyamani?"
- After 2 hours: care signal to configured family payer/caregiver `provider_outage urgent`

### EC-19: User in non-Swiggy city

**Detection:** Onboarding step — call provider.search_skus with empty location → no results
**Handler:**
- During onboarding, this is detected and onboarding is gated: "Sorry, [city] currently not supported. Join waitlist."
- Year 2: ONDC fallback for these cities

### EC-20: Trust attribution

**Detection:** Always-on; this is UX, not a single trigger
**Handler:**
- Every confirmation message names the payer and UPI Request flow plainly: "[Payer name] ki UPI request velthundi; approve aithe order place avtundi. Mee daggara nunchi direct ga money cut avvadu."
- After-order voice: "Amma, ee order [payer name] approve chesaru, ₹X. Order place ayyindi."
- This trust signaling reduces anxiety about who is paying without hiding the payment mechanics.

### EC-21: Webhook duplication (idempotency)

**Already enforced** in `/webhook/whatsapp` SETNX check. Silent skip, return 200.

### EC-22: Mid-flow correction

**Already implemented** in conversation state machine. Any new text or voice message in `AWAITING_CONFIRMATION` cancels in-flight, treats as amendment, re-parses with combined context.

### EC-23: Acknowledge hack failure masking

**Already documented** in Confirmation Agent. Hard 12-second pipeline timeout always fires graceful failure if pipeline hangs.

---

## Hidden Production Edge Cases (Year-1 surprises we want to be ready for)

### EC-24: Voice note longer than Sarvam max length (~30 sec for streaming)

**Handler:** Detect length on download. If >30s, switch to batch processing API. Latency budget extended to 10s, ack message fires earlier.

### EC-25: User sends a forwarded voice note (someone else's voice)

**Detection:** Voice fingerprint mismatch
**Handler:** Confirm: "Amma, idi mee voice kaadu — please malli mee voice tho cheppandi"

### EC-26: Order placed during Swiggy maintenance window

**Detection:** Swiggy returns 503 / specific maintenance code
**Handler:** Same as EC-18 but with specific message: "Swiggy maintenance lo undi, koddi gantalalo malli ready avtundi"

### EC-27: UPI Request approved but webhook delayed

**Detection:** Payment request is still `sent_to_payer`/`initiated`, but Razorpay reconciliation shows the UPI Collect payment succeeded.
**Handler:** Mark `payment_request.status=paid`, continue checkout if the provider cart is still valid, or refund immediately if the cart expired/unavailable. Notify the payer in PWA. Hourly reconciliation job catches this within 1 hour worst case.

### EC-28: User says "naa kodalu" / "naa allullu" (asking about daughter-in-law / son-in-law preferences)

**Handler:** Multi-member household extension covers this. Year 2 feature: richer household-member profiles beyond initial family roles.

### EC-29: Power cut at user's home / no internet

**Detection:** User's last-seen on WhatsApp >12 hours
**Handler:** Care signal `info` to configured family payer/caregiver. Don't escalate unless silence anomaly also fires.

### EC-30: Family payer tries to add a brand-new user without verification

**Handler:** Onboarding gates: new user's phone must verify via WhatsApp OTP from THAT phone, sent to the family payer's number for confirmation. Prevents financial abuse.

---

## Testing Strategy

### Unit tests
- Every parser prompt with known transcriptions → expected ParsedIntent
- Every state transition in convo state machine
- Every edge case handler (EC-01 to EC-30) has at least one test
- ICommerceProvider mock implementation for adapter contract testing

### Integration tests
- Full pipeline against Sarvam sandbox + Anthropic sandbox + mock Swiggy MCP
- Latency assertions: p95 <7s, p99 <10s, no >12s outliers in 100-run batch

### Soak test before going live
- Replay 1000 synthetic voice sessions over 24h
- Monitor: error rate, latency drift, UPI approval/reconciliation drift, queue depth

### Beta test
- 10 real Telugu families recruited from your network
- 4 weeks of real usage
- Daily standup-style review of every voice session that hit confidence <0.85 OR user reported confusion

---

## What an Engineer (or AI Code Editor) Should Always Check Before Merging

1. **Does this code respect the conversation state machine?** No bypassing states.
2. **Does this code go through ICommerceProvider?** No direct Swiggy MCP calls outside the adapter.
3. **Does this redact PII before Claude calls?** Phone, name, address.
4. **Does this enforce idempotency?** Webhook handlers, provider calls, UPI ops.
5. **Does this handle Sarvam being down?** Gemini fallback path.
6. **Does this handle Swiggy MCP being down?** Graceful degradation message.
7. **Does this respect the latency budget?** No new sync blocking calls in the hot path.
8. **Does this end the voice session with a defined outcome?** No silent state.
9. **Does this preserve user agency?** No family payer override of confirmed user decisions.
10. **Does this preserve existing brand preference over partnership?** Document the override path.
