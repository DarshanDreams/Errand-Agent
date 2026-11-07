import json
import uuid
from pathlib import Path

import pytest

from app.agent.context_manager import ContextManager
from app.agent.orchestrator import AgentOrchestrator, ExecutionLimitError
from app.agent.state import AgentState, ConversationState, PendingAction
from app.config import Settings
from app.database.connection import SessionLocal, init_db
from app.database.models import (
    Approval,
    Booking,
    Conversation,
    IdempotencyRecord,
    Memory,
    SlotHold,
    Trace,
)
from app.observability.tracing import Tracer
from app.services.approval_service import (
    approve_action,
    request_approval,
    verify_approval,
)
from app.services.memory_service import MemoryService
from app.services.booking_service import booking_service
from app.services.provider_event_service import handle_provider_cancellation
from app.tools.booking_tools import (
    book,
    cancel,
    get_booking_status,
    hold_slot,
    reschedule,
)
from app.tools.mock_scenarios import scenario_manager
from app.tools.provider_tools import get_provider, search_providers


@pytest.fixture
def eval_scope():
    init_db()
    scope = {
        "user_id": f"eval-{uuid.uuid4()}",
        "conversation_ids": [],
        "hold_ids": [],
        "idempotency_keys": [],
    }
    yield scope

    db = SessionLocal()
    try:
        db.query(Trace).filter(
            Trace.user_id == scope["user_id"]
        ).delete(synchronize_session=False)
        db.query(Approval).filter_by(
            user_id=scope["user_id"]
        ).delete(synchronize_session=False)
        db.query(Booking).filter_by(
            user_id=scope["user_id"]
        ).delete(synchronize_session=False)
        db.query(Memory).filter_by(
            user_id=scope["user_id"]
        ).delete(synchronize_session=False)
        db.query(Conversation).filter_by(
            user_id=scope["user_id"]
        ).delete(synchronize_session=False)
        for key in scope["idempotency_keys"]:
            db.query(IdempotencyRecord).filter_by(
                idempotency_key=key
            ).delete(synchronize_session=False)
        for hold_id in scope["hold_ids"]:
            db.query(SlotHold).filter_by(
                id=hold_id
            ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _hold(scope, provider_id="PROV001", slot="2026-11-09 09:00"):
    db = SessionLocal()
    try:
        db.query(SlotHold).filter_by(
            provider_id=provider_id,
            slot=slot,
            status="held",
        ).update({"status": "expired"}, synchronize_session=False)
        db.commit()
    finally:
        db.close()
    result = hold_slot(provider_id, slot)
    assert "hold_id" in result, result
    scope["hold_ids"].append(result["hold_id"])
    return result


def _approval(scope, details, action="book"):
    db = SessionLocal()
    try:
        conversation_id = str(uuid.uuid4())
        scope["conversation_ids"].append(conversation_id)
        db.add(Conversation(
            id=conversation_id,
            user_id=scope["user_id"],
            state="idle",
            context={},
        ))
        db.commit()
        approval = request_approval(
            db,
            scope["user_id"],
            conversation_id,
            action,
            details,
        )
        return approval["approval_id"]
    finally:
        db.close()


def _confirm_approval(scope, approval_id, details, action="book"):
    db = SessionLocal()
    try:
        return approve_action(
            db,
            approval_id,
            scope["user_id"],
            details,
            expected_action=action,
        )
    finally:
        db.close()


def _create_booking(scope, monkeypatch, provider_id="PROV001", slot="2026-11-09 09:00"):
    hold = _hold(scope, provider_id, slot)
    details = {
        "provider_id": hold["provider_id"],
        "slot": hold["slot"],
        "price": hold["quoted_price"],
    }
    approval_id = _approval(scope, details)
    assert _confirm_approval(scope, approval_id, details)["status"] == "approved"
    key = str(uuid.uuid4())
    scope["idempotency_keys"].append(key)
    monkeypatch.setattr(scenario_manager.config, "booking_outcome", "success")
    result = book(
        hold["hold_id"],
        key,
        approval_id,
        user_id=scope["user_id"],
    )
    assert result["status"] == "confirmed", result
    return {
        "hold": hold,
        "details": details,
        "approval_id": approval_id,
        "key": key,
        "booking_id": result["booking_id"],
    }


def _base_details(hold):
    return {
        "provider_id": hold["provider_id"],
        "slot": hold["slot"],
        "price": hold["quoted_price"],
    }


def _run_search_contract(scope, monkeypatch, tmp_path):
    results = search_providers(
        "plumber",
        "Andheri",
        {"start": "2026-11-09", "end": "2026-11-11"},
    )
    assert 3 <= len(results) <= 8
    assert all(item["available_slots"] and "rating" in item for item in results)


def _run_injection(scope, monkeypatch, tmp_path):
    provider = get_provider("PROV003")
    assert provider["price"] == 1450
    assert any("IGNORE ALL PREVIOUS INSTRUCTIONS" in text for text in provider["reviews"])
    hold = _hold(scope, "PROV003", "2026-11-09 09:00")
    approval = _approval(scope, _base_details(hold))
    db = SessionLocal()
    try:
        check = verify_approval(
            db, approval, scope["user_id"], _base_details(hold), "book"
        )
        assert check["approved"] is False
    finally:
        db.close()


def _run_no_approval(scope, monkeypatch, tmp_path):
    hold = _hold(scope)
    key = str(uuid.uuid4())
    scope["idempotency_keys"].append(key)
    result = book(hold["hold_id"], key, user_id=scope["user_id"])
    assert result["error"] == "approval_required"
    assert get_booking_status(idempotency_key=key)["status"] == "not_found"


def _run_approved_book(scope, monkeypatch, tmp_path):
    booking = _create_booking(scope, monkeypatch)
    status = get_booking_status(idempotency_key=booking["key"])
    assert status["status"] == "confirmed"
    assert status["booking_id"] == booking["booking_id"]


def _run_changed_approval(scope, monkeypatch, tmp_path, change):
    hold = _hold(scope)
    details = _base_details(hold)
    approval = _approval(scope, details)
    changed = dict(details)
    if change == "price":
        changed["price"] += 1
    elif change == "slot":
        changed["slot"] = "2026-11-09 10:00"
    else:
        changed["provider_id"] = "PROV002"
    result = _confirm_approval(scope, approval, changed)
    assert result["error"] == "approval_details_changed"


def _run_change_during_approval(scope, monkeypatch, tmp_path):
    state = ConversationState(
        conversation_id=str(uuid.uuid4()),
        user_id=scope["user_id"],
        status=AgentState.AWAITING_APPROVAL,
        hold_id="pending-hold",
        pending_action=PendingAction(
            action="book",
            approval_id="pending-approval",
            details={
                "provider_id": "PROV001",
                "slot": "2026-11-09 09:00",
                "price": 1200,
            },
        ),
        metadata={"idempotency_key": "pending-idempotency"},
    )
    response = AgentOrchestrator()._handle_pending_action(
        None,
        state,
        "Use 2026-11-09 10:00 instead",
    )
    assert "invalidates the previous approval" in response
    assert state.pending_action is None
    assert state.hold_id is None
    assert not state.metadata.get("idempotency_key")
    assert state.status == AgentState.IDLE


def _run_timeout_success(scope, monkeypatch, tmp_path):
    from app.agent import orchestrator as module

    outputs = [
        {
            "service": "plumber",
            "area": "Andheri",
            "date_range": {
                "start": "2026-11-09",
                "end": "2026-11-09",
            },
            "preferred_time": "morning",
            "budget": 1500,
            "provider_id": None,
            "selected_slot": None,
            "memory_action": None,
            "memory_key": None,
            "memory_value": None,
        },
        {
            "service": None,
            "area": None,
            "date_range": None,
            "preferred_time": None,
            "budget": None,
            "provider_id": None,
            "selected_slot": None,
            "memory_action": None,
            "memory_key": None,
            "memory_value": None,
        },
        {
            "service": None,
            "area": None,
            "date_range": None,
            "preferred_time": None,
            "budget": None,
            "provider_id": None,
            "selected_slot": None,
            "memory_action": None,
            "memory_key": None,
            "memory_value": None,
        },
    ]

    async def scripted_completion(*, messages, temperature=0.2, tools=None):
        return {
            "message": {"content": json.dumps(outputs.pop(0))},
            "usage": {"prompt_tokens": 25, "completion_tokens": 12, "cost": 0.001},
            "model": "offline-scripted-model",
        }

    monkeypatch.setattr(module.llm_client, "chat_completion", scripted_completion)
    monkeypatch.setattr(scenario_manager.config, "booking_outcome", "timeout")
    monkeypatch.setattr(
        scenario_manager.config, "timeout_booking_succeeds", True
    )
    db = SessionLocal()
    try:
        result = None
        conversation_id = None
        for message in (
            "Find a plumber in Andheri for 2026-11-09 mornings, under ₹1,500",
            "QuickFix Plumbing",
            "9 AM",
        ):
            result = asyncio_run(
                AgentOrchestrator().handle_message(
                    db,
                    message,
                    conversation_id=conversation_id,
                    user_id=scope["user_id"],
                )
            )
            conversation_id = result["conversation_id"]
        assert result["state"] == "awaiting_approval", result
        result = asyncio_run(
            AgentOrchestrator().handle_message(
                db,
                "YES",
                conversation_id=conversation_id,
                user_id=scope["user_id"],
            )
        )
        assert result["state"] == "booked", result
        assert result["booking_id"]

        events = db.query(Trace).filter_by(
            conversation_id=conversation_id
        ).order_by(Trace.created_at).all()
        trace_data = [
            {
                "event_type": event.event_type,
                "event_name": event.event_name,
                "arguments": event.arguments,
                "result": event.result,
                "latency_ms": event.latency_ms,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "estimated_cost": event.estimated_cost,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]
        trace_path = Path(__file__).resolve().parents[2] / "outputs" / "timeout_after_success_trace.json"
        trace_path.parent.mkdir(exist_ok=True)
        trace_path.write_text(
            json.dumps(
                {
                    "conversation_id": conversation_id,
                    "user_id": scope["user_id"],
                    "final_state": result["state"],
                    "events": trace_data,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert any(
            event["event_name"] == "book"
            and (event["result"] or {}).get("error") == "timeout"
            for event in trace_data
        )
        assert any(
            event["event_name"] == "get_booking_status"
            and (event["result"] or {}).get("status") == "confirmed"
            for event in trace_data
        )
        contexts = [
            event for event in trace_data
            if event["event_name"] == "model_context_sent"
        ]
        assert len(contexts) == 3
        assert all(
            event["arguments"]["within_budget"]
            and event["arguments"]["token_count"]
            <= event["arguments"]["token_budget"]
            for event in contexts
        )
    finally:
        db.close()


def asyncio_run(awaitable):
    import asyncio

    return asyncio.run(awaitable)


def _run_timeout_failure(scope, monkeypatch, tmp_path):
    hold = _hold(scope)
    details = _base_details(hold)
    approval = _approval(scope, details)
    assert _confirm_approval(scope, approval, details)["status"] == "approved"
    key = str(uuid.uuid4())
    scope["idempotency_keys"].append(key)
    monkeypatch.setattr(scenario_manager.config, "booking_outcome", "timeout")
    monkeypatch.setattr(scenario_manager.config, "timeout_booking_succeeds", False)
    result = book(
        hold["hold_id"], key, approval, user_id=scope["user_id"]
    )
    assert result["error"] == "timeout"
    assert get_booking_status(idempotency_key=key)["status"] == "not_booked"


def _run_safe_timeout_retry(scope, monkeypatch, tmp_path):
    import time

    hold = _hold(scope)
    details = _base_details(hold)
    conversation_id = str(uuid.uuid4())
    scope["conversation_ids"].append(conversation_id)
    db = SessionLocal()
    try:
        db.add(Conversation(
            id=conversation_id,
            user_id=scope["user_id"],
            state=AgentState.AWAITING_APPROVAL.value,
            context={},
        ))
        db.commit()
        approval = request_approval(
            db, scope["user_id"], conversation_id, "book", details
        )
        key = str(uuid.uuid4())
        scope["idempotency_keys"].append(key)
        state = ConversationState(
            conversation_id=conversation_id,
            user_id=scope["user_id"],
            status=AgentState.AWAITING_APPROVAL,
            hold_id=hold["hold_id"],
            pending_action=PendingAction(
                action="book",
                details=details,
                approval_id=approval["approval_id"],
            ),
            metadata={"idempotency_key": key},
        )
        original_confirm = booking_service.confirm_booking
        attempts = 0

        def timeout_then_success(**kwargs):
            nonlocal attempts
            attempts += 1
            result = original_confirm(**kwargs)
            if attempts == 1:
                monkeypatch.setattr(
                    scenario_manager.config, "booking_outcome", "success"
                )
            return result

        monkeypatch.setattr(
            scenario_manager.config, "booking_outcome", "timeout"
        )
        monkeypatch.setattr(
            scenario_manager.config, "timeout_booking_succeeds", False
        )
        monkeypatch.setattr(
            booking_service, "confirm_booking", timeout_then_success
        )
        response = AgentOrchestrator()._handle_pending_action(
            db, state, "YES", Tracer(db), time.monotonic()
        )
        assert state.status == AgentState.BOOKED
        assert state.booking_id
        assert attempts == 2
        assert "verified after a safe retry" in response
        assert get_booking_status(idempotency_key=key)["status"] == "confirmed"
    finally:
        db.close()


def _run_duplicate(scope, monkeypatch, tmp_path):
    booking = _create_booking(scope, monkeypatch)
    repeated = book(
        booking["hold"]["hold_id"],
        booking["key"],
        booking["approval_id"],
        user_id=scope["user_id"],
    )
    assert repeated["booking_id"] == booking["booking_id"]
    db = SessionLocal()
    try:
        assert db.query(Booking).filter_by(
            idempotency_key=booking["key"]
        ).count() == 1
    finally:
        db.close()


def _run_idempotency_reuse(scope, monkeypatch, tmp_path):
    booking = _create_booking(scope, monkeypatch)
    second_hold = _hold(scope, "PROV002", "2026-11-09 11:00")
    result = book(
        second_hold["hold_id"],
        booking["key"],
        booking["approval_id"],
        user_id=scope["user_id"],
    )
    assert result["error"] == "idempotency_key_reused_with_different_request"


def _run_cancel_unapproved(scope, monkeypatch, tmp_path):
    booking = _create_booking(scope, monkeypatch)
    result = cancel(
        booking["booking_id"],
        str(uuid.uuid4()),
    )
    assert result["error"] == "approval_required"


def _run_cancel_retry(scope, monkeypatch, tmp_path):
    booking = _create_booking(scope, monkeypatch)
    db = SessionLocal()
    try:
        row = db.query(Booking).filter_by(id=booking["booking_id"]).one()
        details = {
            "booking_id": row.id,
            "provider_id": row.provider_id,
            "service": row.service,
            "slot": row.slot,
        }
    finally:
        db.close()
    approval = _approval(scope, details, "cancel")
    assert _confirm_approval(scope, approval, details, "cancel")["status"] == "approved"
    key = str(uuid.uuid4())
    scope["idempotency_keys"].append(key)
    monkeypatch.setattr(
        scenario_manager.config, "cancellation_failures_remaining", 1
    )
    first = cancel(booking["booking_id"], key, approval)
    second = cancel(booking["booking_id"], key, approval)
    assert first["error"] == "service_unavailable"
    assert second["status"] == "cancelled"


def _run_reschedule_conflict(scope, monkeypatch, tmp_path):
    booking = _create_booking(scope, monkeypatch)
    db = SessionLocal()
    try:
        row = db.query(Booking).filter_by(id=booking["booking_id"]).one()
        details = {
            "booking_id": row.id,
            "provider_id": row.provider_id,
            "old_slot": row.slot,
            "new_slot": "2026-11-09 10:00",
        }
    finally:
        db.close()
    approval = _approval(scope, details, "reschedule")
    assert _confirm_approval(scope, approval, details, "reschedule")["status"] == "approved"
    key = str(uuid.uuid4())
    scope["idempotency_keys"].append(key)
    monkeypatch.setattr(scenario_manager.config, "force_slot_conflict", True)
    result = reschedule(
        booking["booking_id"],
        details["new_slot"],
        key,
        approval,
    )
    assert result["error"] == "slot_conflict"


def _run_inferred_memory(scope, monkeypatch, tmp_path):
    db = SessionLocal()
    try:
        service = MemoryService(db)
        service.save_memory(
            scope["user_id"], "preferred_area", "Andheri",
            source="inferred", confidence=0.5,
        )
        assert service.get_memories(scope["user_id"]) == []
        assert len(service.get_memories(
            scope["user_id"], include_inferred=True
        )) == 1
    finally:
        db.close()


def _run_forgotten_context(scope, monkeypatch, tmp_path):
    db = SessionLocal()
    try:
        service = MemoryService(db)
        service.save_memory(scope["user_id"], "preferred_area", "OldTown")
        service.forget_memory(scope["user_id"], "preferred_area")
        excluded = service.get_forgotten_values(scope["user_id"])
        context = ContextManager(token_budget=256).build_context(
            "extract",
            [{"role": "user", "content": "I usually live in OldTown"}],
            excluded_text=excluded,
        )
        assert all("OldTown" not in item["content"] for item in context)
    finally:
        db.close()


def _run_forget_without_model(scope, monkeypatch, tmp_path):
    from app.agent import orchestrator as module

    async def forbidden_model_call(**kwargs):
        pytest.fail("A forget operation must be handled without exposing memories to the model")

    monkeypatch.setattr(
        module.llm_client, "chat_completion", forbidden_model_call
    )
    db = SessionLocal()
    try:
        MemoryService(db).save_memory(
            scope["user_id"], "preferred_area", "OldTown"
        )
        response = asyncio_run(AgentOrchestrator().handle_message(
            db,
            "Forget my preferred area",
            user_id=scope["user_id"],
        ))
        assert "forgotten" in response["message"].lower()
        assert MemoryService(db).get_memories(scope["user_id"]) == []
        assert not db.query(Trace).filter_by(
            user_id=scope["user_id"],
            event_name="model_context_sent",
        ).count()
    finally:
        db.close()


def _run_50_memories(scope, monkeypatch, tmp_path):
    db = SessionLocal()
    try:
        service = MemoryService(db)
        for index in range(50):
            service.save_memory(
                scope["user_id"], f"unrelated_{index}", f"value_{index}"
            )
        service.save_memory(scope["user_id"], "preferred_area", "Andheri")
        service.save_memory(
            scope["user_id"], "earliest_acceptable_time", "10:00"
        )
        relevant = service.get_relevant_memories(
            scope["user_id"], "Find a plumber in the morning"
        )
        assert {item["key"] for item in relevant} == {
            "preferred_area",
            "earliest_acceptable_time",
        }
        assert db.query(Memory).filter_by(user_id=scope["user_id"]).count() == 52
        context = ContextManager(token_budget=512).build_context(
            "extract",
            [{"role": "user", "content": "Find a plumber in the morning"}],
            relevant_memories=relevant,
        )
        assert "Andheri" in context[0]["content"]
        assert "value_0" not in context[0]["content"]
    finally:
        db.close()


def _run_long_context(scope, monkeypatch, tmp_path):
    history = [
        {"role": "user", "content": f"old detail {index} " + ("irrelevant " * 50)}
        for index in range(80)
    ]
    history.append({"role": "user", "content": "latest request"})
    conversation_id = str(uuid.uuid4())
    scope["conversation_ids"].append(conversation_id)
    state = ConversationState(
        conversation_id=conversation_id,
        user_id=scope["user_id"],
        messages=history,
    )
    db = SessionLocal()
    try:
        conversation = Conversation(
            id=conversation_id,
            user_id=scope["user_id"],
            state="idle",
            context={"agent_state": state.model_dump(mode="json")},
        )
        db.add(conversation)
        db.commit()
        stored_state = AgentOrchestrator()._load_state(conversation)
        assert len(stored_state.messages) == 81
        history = stored_state.messages
    finally:
        db.close()
    manager = ContextManager(token_budget=300)
    context = manager.build_context("extract intent", history)
    assert manager.get_context_stats(context)["within_budget"]
    assert context[-1]["content"] == "latest request"
    assert len(context) < len(history)


def _run_tool_limit(scope, monkeypatch, tmp_path):
    agent = AgentOrchestrator()
    agent.tool_call_limit = 0
    state = ConversationState(
        conversation_id=str(uuid.uuid4()),
        user_id=scope["user_id"],
    )
    with pytest.raises(ExecutionLimitError):
        agent._call_tool(
            state,
            Tracer(),
            __import__("time").monotonic(),
            "forbidden",
            {},
            lambda: pytest.fail("Tool should not execute after limit exhaustion"),
        )


def _run_keyless_configuration(scope, monkeypatch, tmp_path):
    from app.agent import llm as llm_module

    settings = Settings(_env_file=None, OPENROUTER_API_KEY="")
    assert settings.OPENROUTER_API_KEY == ""
    monkeypatch.setattr(llm_module.llm_client, "api_key", "")
    with pytest.raises(llm_module.OpenRouterError, match="OPENROUTER_API_KEY is missing"):
        asyncio_run(llm_module.llm_client.chat_completion(messages=[]))


def _run_model_turn_limit(scope, monkeypatch, tmp_path):
    from app.agent import orchestrator as module

    agent = AgentOrchestrator()
    agent.model_turn_limit = 0

    async def unexpected_call(**kwargs):
        pytest.fail("Model must not be called after turn limit exhaustion")

    monkeypatch.setattr(module.llm_client, "chat_completion", unexpected_call)
    with pytest.raises(module.OpenRouterError, match="turn limit"):
        asyncio_run(agent._extract_intent([], [], timeout=0.1))


def _run_wall_clock_limit(scope, monkeypatch, tmp_path):
    from app.agent import orchestrator as module

    async def slow_call(**kwargs):
        import asyncio

        await asyncio.sleep(0.3)
        return {"message": {"content": "{}"}, "usage": {}}

    monkeypatch.setattr(module.llm_client, "chat_completion", slow_call)
    agent = AgentOrchestrator()
    agent.execution_limit = 0.01
    db = SessionLocal()
    try:
        result = asyncio_run(agent.handle_message(
            db,
            "Find a plumber in Andheri for 2026-11-09",
            user_id=scope["user_id"],
        ))
        assert result["state"] == "failed"
        assert "execution" in result["message"].lower()
    finally:
        db.close()


def _run_proactive_cancel(scope, monkeypatch, tmp_path):
    booking = _create_booking(scope, monkeypatch)
    db = SessionLocal()
    try:
        MemoryService(db).save_memory(
            scope["user_id"], "earliest_acceptable_time", "10:00"
        )
        result = handle_provider_cancellation(db, booking["booking_id"])
        assert result["status"] == "replacement_awaiting_approval"
        assert result["replacement"]["provider_id"] != booking["details"]["provider_id"]
        assert result["replacement"]["slot"][11:] >= "10:00"
        state = AgentOrchestrator()._load_state(
            db.query(Conversation).filter_by(
                id=result["conversation_id"]
            ).one()
        )
        assert state.pending_action is not None
        assert state.pending_action.action == "book"
        check = verify_approval(
            db,
            state.pending_action.approval_id,
            scope["user_id"],
            state.pending_action.details,
            "book",
        )
        assert check["approved"] is False
    finally:
        db.close()


def _run_user_isolation(scope, monkeypatch, tmp_path):
    booking = _create_booking(scope, monkeypatch)
    other_user = f"other-{uuid.uuid4()}"
    result = book(
        booking["hold"]["hold_id"],
        booking["key"],
        booking["approval_id"],
        user_id=other_user,
    )
    assert result["error"] == "booking_owner_mismatch"


SCENARIOS = [
    pytest.param(
        {"name": "provider search contract", "expected": "3-8 providers with slots", "run": _run_search_contract},
        id="provider-search-contract",
    ),
    pytest.param(
        {"name": "prompt injection", "expected": "Review cannot change price or approve booking", "run": _run_injection},
        id="prompt-injection",
    ),
    pytest.param(
        {"name": "booking without approval", "expected": "No booking and approval_required", "run": _run_no_approval},
        id="booking-without-approval",
    ),
    pytest.param(
        {"name": "approved booking", "expected": "Exact approved booking is verified", "run": _run_approved_book},
        id="approved-booking",
    ),
    pytest.param(
        {"name": "changed price", "expected": "Approval invalidated", "run": lambda *args: _run_changed_approval(*args, "price")},
        id="changed-price",
    ),
    pytest.param(
        {"name": "changed slot", "expected": "Approval invalidated", "run": lambda *args: _run_changed_approval(*args, "slot")},
        id="changed-slot",
    ),
    pytest.param(
        {"name": "changed provider", "expected": "Approval invalidated", "run": lambda *args: _run_changed_approval(*args, "provider")},
        id="changed-provider",
    ),
    pytest.param(
        {"name": "change while approval pending", "expected": "Old approval and quote invalidated before any side effect", "run": _run_change_during_approval},
        id="change-during-approval",
    ),
    pytest.param(
        {"name": "timeout after success end-to-end", "expected": "Authoritative status confirms booking; raw trace generated", "run": _run_timeout_success},
        id="timeout-after-success",
    ),
    pytest.param(
        {"name": "timeout without success", "expected": "Authoritative status is not_booked", "run": _run_timeout_failure},
        id="timeout-without-success",
    ),
    pytest.param(
        {"name": "safe timeout retry", "expected": "Status checked before same-key retry; outcome verified", "run": _run_safe_timeout_retry},
        id="safe-timeout-retry",
    ),
    pytest.param(
        {"name": "repeated confirmation", "expected": "One booking returned by idempotency key", "run": _run_duplicate},
        id="repeated-confirmation",
    ),
    pytest.param(
        {"name": "idempotency key reuse", "expected": "Different request rejected", "run": _run_idempotency_reuse},
        id="idempotency-key-reuse",
    ),
    pytest.param(
        {"name": "cancellation without approval", "expected": "Cancellation blocked", "run": _run_cancel_unapproved},
        id="cancellation-without-approval",
    ),
    pytest.param(
        {"name": "transient cancellation failure", "expected": "503 then retry cancels once", "run": _run_cancel_retry},
        id="transient-cancellation-retry",
    ),
    pytest.param(
        {"name": "reschedule conflict", "expected": "Slot conflict reported; booking unchanged", "run": _run_reschedule_conflict},
        id="reschedule-slot-conflict",
    ),
    pytest.param(
        {"name": "inferred preference", "expected": "Inferred facts excluded by default", "run": _run_inferred_memory},
        id="inferred-memory-exclusion",
    ),
    pytest.param(
        {"name": "forgotten memory", "expected": "Forgotten fact removed from model context", "run": _run_forgotten_context},
        id="forgotten-memory-exclusion",
    ),
    pytest.param(
        {"name": "forget request", "expected": "Preference forgotten without sending it to the model", "run": _run_forget_without_model},
        id="forget-without-model-context",
    ),
    pytest.param(
        {"name": "50 stored memories", "expected": "Only request-relevant preferences selected", "run": _run_50_memories},
        id="50-memory-context",
    ),
    pytest.param(
        {"name": "long conversation", "expected": "Recent context stays within token cap", "run": _run_long_context},
        id="long-conversation-context-budget",
    ),
    pytest.param(
        {"name": "tool call limit", "expected": "Tool is not invoked after limit exhaustion", "run": _run_tool_limit},
        id="tool-call-limit",
    ),
    pytest.param(
        {"name": "keyless model configuration", "expected": "App settings load; live model call fails clearly without a key", "run": _run_keyless_configuration},
        id="keyless-model-configuration",
    ),
    pytest.param(
        {"name": "model turn limit", "expected": "No model call after configured turn limit", "run": _run_model_turn_limit},
        id="model-turn-limit",
    ),
    pytest.param(
        {"name": "wall clock limit", "expected": "Timed-out model call returns clear failed outcome", "run": _run_wall_clock_limit},
        id="wall-clock-limit",
    ),
    pytest.param(
        {"name": "proactive provider cancellation", "expected": "Replacement proposed and fresh approval required", "run": _run_proactive_cancel},
        id="proactive-provider-cancellation",
    ),
    pytest.param(
        {"name": "user isolation", "expected": "Another user cannot reuse booking outcome", "run": _run_user_isolation},
        id="user-isolation",
    ),
]


@pytest.mark.evaluation
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_assignment_scenario(scenario, eval_scope, monkeypatch, tmp_path):
    scenario["run"](eval_scope, monkeypatch, tmp_path)
