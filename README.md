# Errand Agent

A locally runnable chat agent for arranging home-service errands. Provider search,
holds, bookings, cancellation, rescheduling, and provider-cancellation events are
deterministic local mocks; no payment or real home-service provider is contacted.

## Run locally

1. Use Python 3.11 or newer and install the dependencies:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`. Add an OpenRouter API key to use the real model
   for natural-language extraction. The mock provider environment remains local.
   The API starts without a key, but chat requests that need model extraction
   return a clear unavailable response. Automated evaluations use a scripted
   offline model and never contact OpenRouter.

3. Start the API:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

   Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

## API examples

```http
POST /chat
Content-Type: application/json

{"user_id":"demo-user","message":"Find a plumber in Andheri for 2026-11-09 mornings, under ₹1,500"}
```

Continue the conversation by including the returned `conversation_id`.
The agent collects required details, lists matching mock providers and slots,
holds the selected slot, and asks for explicit approval of the final quote.
Booking, cancellation, and rescheduling are never authorized by model output.

Saved facts can be inspected, corrected, and forgotten through:

- `GET /users/{user_id}/memories`
- `PUT /users/{user_id}/memories/{key}` with `{"value":"..."}`
- `DELETE /users/{user_id}/memories/{key}`

To simulate a provider cancelling without a user prompt, call
`POST /events/provider-cancelled` with `{"booking_id":"..."}`. The agent marks
the old booking cancelled, finds a replacement within known preferences where
possible, and returns a fresh approval request; it does not rebook automatically.

## Development and evaluation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The 27 deterministic assignment scenarios are marked and run offline. The test
run generates:

- `outputs/eval_results.csv` — scenario, expected outcome, actual outcome, and pass/fail.
- `outputs/timeout_after_success_trace.json` — sanitized trace from a complete
  scripted booking where the booking call times out after succeeding.

SQLite data is stored in `data/errand_agent.db`. To inspect traces in the
SQLite Explorer, open the `trace_browser` view; it shows the newest records
first in local time and limits argument/result previews to keep the columns readable.

