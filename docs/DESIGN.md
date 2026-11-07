# Design note

## Workflow and safety

One persisted `ConversationState` drives collection, search, provider/slot
selection, approval, booking, recovery, and completion. Required service, area,
and date details are collected once; corrections invalidate stale search
results. Budget and time constraints filter candidate prices and slots. Provider
reviews are logged and presented only as untrusted data.

Booking, cancellation, and rescheduling require a database approval record.
The record fingerprints the exact material details; the application verifies
the fingerprint and action immediately before the side effect. A changed
provider, slot, or quote cannot reuse approval. A ten-minute hold precedes
approval. Every booking uses a persistent idempotency key. A timeout is resolved
through `get_booking_status`; an uncertain result is never reported as success
or retried with a new key.

The `/events/provider-cancelled` local event endpoint handles provider
cancellations independently of user chat. It marks the original booking as
cancelled, considers explicit saved preferences, and creates a replacement
proposal requiring new user approval. It never silently rebooks.

## Memory, limits, and observability

Only active, explicit, request-relevant preferences enter model context.
Forget requests are handled before model invocation, and forgotten values are
removed from historical turns before later context construction.
`ContextManager` caps context at `CONTEXT_TOKEN_BUDGET` (4,000 by default) and
keeps recent messages. The orchestrator allows one model extraction per chat
turn, enforces configured tool-call and wall-clock limits, and returns a clear
failure outcome. Evaluation uses a scripted model; live chat uses OpenRouter.

Each run traces state transitions, context sent and token count, model usage,
tool arguments/results/latency, and final outcome. Trace payloads are sanitized
and bounded. The full sanitized timeout trace is in
`../outputs/timeout_after_success_trace.json`.

**Timeout-after-success excerpt (from the generated trace):**

```text
book               -> {"error":"timeout"}
get_booking_status -> {"status":"confirmed","provider_id":"PROV001",
                       "slot":{"datetime":"2026-11-09 09:00"}}
request_completed  -> {"state":"booked"}
```

The booking result is reported only after the authoritative status check. The
recorded model contexts were 382, 396, and 409 tokens, each below the 4,000-token
budget. SQLite and in-process seeded mocks keep the assignment runnable locally;
the trade-off is that simulated availability is finite and date-specific.