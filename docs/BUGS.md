# Bugs found and regression coverage

1. **User identity was overwritten.** The orchestrator replaced every supplied
   `user_id` with `demo-user`, mixing conversations and booking ownership.
   The caller identity is now preserved through booking creation and status
   lookup; an idempotency result belonging to another user is rejected.
   Regression: `user-isolation` in `tests/evaluation/test_assignment_scenarios.py`.

2. **The context budget was bypassed.** Intent extraction sent raw recent history
   and all stored memories directly to the model instead of using the existing
   `ContextManager`. This could exceed the token cap and include irrelevant or
   forgotten details. Extraction now uses bounded context, explicit relevant
   preferences, and filters forgotten values from old turns.
   Regression: `50-memory-context`, `long-conversation-context-budget`, and
   `forgotten-memory-exclusion` in the assignment evaluation.

3. **Keyless local startup depended on configuration being present.**
   `OPENROUTER_API_KEY` was required while importing settings, preventing app
   and offline test startup when no `.env` existed. It now defaults to empty;
   the model client reports a clear missing-key error only when live extraction
   is requested. Regression: `keyless-model-configuration` in the assignment
   evaluation.