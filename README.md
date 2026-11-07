# Errand Agent

Errand Agent is a local chat API for arranging home-service errands. It gathers
missing details, searches a small mock provider catalog, compares available
providers and slots, asks for explicit approval before consequential actions,
and verifies booking outcomes. Providers, booking, payments, and provider events
are simulated; it does not contact real service providers or process payments.

## Features

- Persistent conversations and workflow state backed by SQLite.
- Natural-language extraction through an OpenRouter chat-completions model.
- Mock provider search for plumbers, electricians, and AC servicing.
- Explicit application-enforced approval for booking, cancellation, and
  rescheduling. Approval applies to the exact action details.
- Ten-minute slot holds, idempotent booking, and status checks after ambiguous
  timeouts to avoid duplicate bookings or unverified success claims.
- Simulated slot conflicts and transient cancellation failures.
- Explicit user memories that can be inspected, corrected, and forgotten.
- Context token limits, request/tool/model traces, and a compact database view
  for browsing recent trace events.

## Requirements

- Python 3.11 or newer.
- An OpenRouter API key for live natural-language chat. The application and
  offline tests can start without a key, but live chat extraction requires one.

## Install and run on Windows

Run these commands from the project root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `OPENROUTER_API_KEY` to your own key. Do not commit `.env`.
Then start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The API listens at `http://127.0.0.1:8000`. Interactive API documentation is
available at `http://127.0.0.1:8000/docs`; a database health check is at
`http://127.0.0.1:8000/health`.

The SQLite database is initialized automatically at
`data/errand_agent.db`. Run the app from the project root so the default
relative database path resolves to that file.

## Configuration

Settings are read from `.env`; defaults are in [`.env.example`](.env.example).

| Variable | Purpose | Default |
|---|---|---|
| `OPENROUTER_API_KEY` | Credential for live model requests | Empty |
| `OPENROUTER_MODEL` | OpenRouter model identifier | `openai/gpt-4o-mini` |
| `OPENROUTER_BASE_URL` | OpenRouter-compatible API base URL | `https://openrouter.ai/api/v1` |
| `DATABASE_URL` | SQLAlchemy database URL | `sqlite:///./data/errand_agent.db` |
| `MAX_MODEL_TURNS` | Maximum model-turn setting; normal chat currently makes one extraction request | `5` |
| `MAX_TOOL_CALLS` | Tool-call limit per chat turn | `10` |
| `MAX_EXECUTION_SECONDS` | Chat-turn wall-clock limit | `30` |
| `CONTEXT_TOKEN_BUDGET` | Maximum model context size | `4000` |
| `HOLD_DURATION_MINUTES` | Slot hold duration | `10` |
| `ENABLE_TRACING` | Enable persisted trace events | `true` |

## Chat workflow

Send a message to `POST /chat`. Include the returned `conversation_id` on
subsequent turns to continue the same persisted conversation. `user_id` is
optional and defaults to `demo-user`.

PowerShell example:

```powershell
$body = @{
    user_id = "demo-user"
    message = "Find a plumber in Andheri for 2026-11-09 mornings, under ₹1,500"
} | ConvertTo-Json

$reply = Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/chat" `
    -ContentType "application/json" `
    -Body $body

$reply
$conversationId = $reply.conversation_id
```

The agent asks only for required details it cannot determine, then shows
matching mock providers. Select a provider and slot in follow-up messages. The
agent places a temporary hold and displays the final quoted price; reply `YES`
to approve those exact details or `NO` to decline. A change to the provider,
slot, or quoted price requires fresh approval. A booking is reported as
successful only after its status is verified.

Continue the conversation by reusing its ID:

```powershell
$body = @{
    user_id = "demo-user"
    conversation_id = $conversationId
    message = "QuickFix Plumbing"
} | ConvertTo-Json

$reply = Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/chat" `
    -ContentType "application/json" `
    -Body $body

$reply
```

Continue similarly to choose an offered time and then explicitly approve or
decline. The response includes `conversation_id`, `state`, `message`,
`booking_id` (when verified), and `execution_seconds`.

Existing bookings can be listed by asking to show or check bookings. Ask to
cancel or reschedule a booking and the agent presents the exact action for
approval. If more than one booking matches, identify the booking reference.
For a reschedule, provide the new date and time as `YYYY-MM-DD HH:MM`. A
cancellation may return a simulated transient 503; retry the same approved
request rather than starting a new action.

## Memory API

Memory endpoints operate on the specified `user_id`. Only explicit active
preferences are returned by default; `include_inferred=true` includes inferred
facts for inspection.

```http
GET    /users/{user_id}/memories
GET    /users/{user_id}/memories?include_inferred=true
PUT    /users/{user_id}/memories/{key}
DELETE /users/{user_id}/memories/{key}
```

To correct a preference, send JSON such as
`{"value":"Bandra"}` to `PUT /users/demo-user/memories/preferred_area`. A
delete deactivates the saved fact; it does not erase its audit history.
Supported preference keys used for relevant context include `preferred_area`,
`preferred_service`, `preferred_time`, `earliest_acceptable_time`,
`preferred_provider`, `preferred_technician`, `maximum_budget`, and `budget`.

## Simulated provider cancellation

`POST /events/provider-cancelled` simulates an external provider cancellation:

```json
{"booking_id":"<confirmed-booking-id>"}
```

The agent marks the existing booking as provider-cancelled, searches for an
available replacement using known explicit preferences where possible, and
returns a new conversation and approval request. It does not book the
replacement without fresh user approval. If there is no suitable replacement,
the response reports that outcome. To respond to the replacement request,
send `YES` or `NO` to `POST /chat` using the returned `conversation_id` and the
same `user_id`.

## Mock behavior and limitations

- Provider names, service areas, prices, ratings, reviews, and available slots
  are defined in `app/tools/provider_tools.py`. This is a finite fixture
  catalog, not live availability; listed dates and slots may be fixed in the
  mock data.
- Booking outcomes are simulated: 70% success, 15% slot taken, and 15% timeout.
  Half of simulated timeouts have actually created a booking. A seeded random
  generator makes the default sequence reproducible within a process; tests
  can override outcomes using `ScenarioConfig`.
- A slot hold lasts ten minutes. The hold's quoted price may be configured to
  differ from the search estimate, and only the held quote is shown for
  approval.
- The first cancellation attempt simulates a transient 503; retries can
  succeed. Rescheduling can return `slot_conflict` for configured slots.
- The malicious provider review is untrusted fixture text. Application code,
  not review content or model output, enforces approval and booking rules.
- The service is intended for local development and demonstration. The API has
  no authentication or authorization layer; do not expose it to an untrusted
  network or use it for real bookings.

## Traces and database inspection

Trace records are written to the `traces` table in `data/errand_agent.db` when
`ENABLE_TRACING=true`. Each record can include event name/type, conversation
and user IDs, sanitized arguments and results, latency, model token usage, and
estimated cost when provided. Request messages are included in run traces, so
protect the local database as you would other conversation data.

The app creates a `trace_browser` SQLite view at startup. In the VS Code
SQLite extension, open `data/errand_agent.db`, expand **Views**, then open
`trace_browser` for newest-first local timestamps and compact argument/result
previews. The `traces` table retains full trace fields. Refresh the SQLite
Explorer and reopen the view after new activity if the displayed results are
stale.

## Tests

Install the requirements, then run all tests from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The automated tests use local mocks and a scripted offline model; they do not
need an OpenRouter key or contact external providers.

## Project layout

```text
app/
  agent/          conversation state, orchestration, prompts, model and context
  database/       SQLAlchemy models, SQLite engine and initialization
  observability/  structured trace recording
  services/       approvals, booking, recovery, memory and provider events
  tools/          local provider and booking mocks, configurable scenarios
  config.py       environment-backed settings
  main.py         FastAPI application and HTTP endpoints
tests/
  evaluation/     deterministic end-to-end scenarios
  test_*.py       approval, memory, booking and recovery tests
data/             local SQLite database
docs/             project documentation
outputs/          local generated files
```
