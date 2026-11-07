import re
import time
import uuid

from sqlalchemy.orm import Session

from app.agent.state import AgentState, ConversationState, PendingAction
from app.database.models import Booking, Conversation
from app.observability.tracing import Tracer
from app.services.approval_service import request_approval
from app.services.memory_service import MemoryService
from app.tools.booking_tools import hold_slot
from app.tools.provider_tools import get_provider, search_providers


def handle_provider_cancellation(db: Session, booking_id: str) -> dict:
    booking = db.query(Booking).filter_by(id=booking_id).first()
    if not booking:
        return {"error": "booking_not_found"}
    if booking.status != "confirmed":
        return {
            "status": "already_processed",
            "booking_id": booking.id,
            "booking_status": booking.status,
        }

    cancelled_provider = get_provider(booking.provider_id)
    if cancelled_provider.get("error"):
        return {"error": "provider_not_found"}

    booking.status = "provider_cancelled"
    db.commit()

    memories = MemoryService(db).get_memories(booking.user_id)
    preferences = {item["key"]: item["value"] for item in memories}
    budget = preferences.get("maximum_budget", preferences.get("budget"))
    try:
        budget = float(budget) if budget is not None else None
    except (TypeError, ValueError):
        budget = None

    earliest = preferences.get(
        "earliest_acceptable_time", preferences.get("preferred_time")
    )
    time_match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?",
        str(earliest or ""),
        re.IGNORECASE,
    )
    earliest_time = None
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or "0")
        ampm = (time_match.group(3) or "").replace(".", "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        earliest_time = f"{hour:02d}:{minute:02d}"

    search_started = time.perf_counter()
    providers = search_providers(
        service=booking.service,
        area=cancelled_provider["area"],
    )
    tracer = Tracer(db)
    tracer.log_event(
        user_id=booking.user_id,
        event_type="tool",
        event_name="search_providers",
        arguments={
            "service": booking.service,
            "area": cancelled_provider["area"],
        },
        result=providers,
        latency_ms=(time.perf_counter() - search_started) * 1000,
    )
    preferred_provider = str(preferences.get("preferred_provider", "")).casefold()
    providers = sorted(
        (
            provider for provider in providers
            if provider["id"] != booking.provider_id
            and (budget is None or float(provider["price"]) <= budget)
        ),
        key=lambda provider: (
            0 if preferred_provider in {
                provider["id"].casefold(),
                provider["name"].casefold(),
            } else 1,
            -float(provider["rating"]),
            float(provider["price"]),
        ),
    )
    replacement = None
    old_slot = booking.slot
    if isinstance(old_slot, dict):
        old_slot = old_slot.get("datetime", "")
    time_preference = str(earliest or "").casefold()
    for provider in providers:
        tool_started = time.perf_counter()
        provider_data = get_provider(provider["id"])
        tracer.log_event(
            user_id=booking.user_id,
            event_type="tool",
            event_name="get_provider",
            arguments={"provider_id": provider["id"]},
            result=provider_data,
            latency_ms=(time.perf_counter() - tool_started) * 1000,
        )
        slots = [
            slot for slot in provider_data["available_slots"]
            if (not old_slot or slot >= old_slot)
            and _slot_matches_preference(slot, time_preference, earliest_time)
        ]
        for slot in sorted(slots):
            tool_started = time.perf_counter()
            hold = hold_slot(provider["id"], slot)
            tracer.log_event(
                user_id=booking.user_id,
                event_type="tool",
                event_name="hold_slot",
                arguments={"provider_id": provider["id"], "slot": slot},
                result=hold,
                latency_ms=(time.perf_counter() - tool_started) * 1000,
            )
            if not hold.get("error"):
                replacement = (provider_data, slot, hold)
                break
        if replacement:
            break

    if replacement is None:
        return {
            "status": "provider_cancelled_no_replacement",
            "booking_id": booking.id,
            "message": (
                "Your provider cancelled. I couldn't find a replacement "
                "within your saved preferences."
            ),
        }

    provider, slot, hold = replacement

    conversation = Conversation(
        id=str(uuid.uuid4()),
        user_id=booking.user_id,
        state=AgentState.AWAITING_APPROVAL.value,
        context={},
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    details = {
        "provider_id": provider["id"],
        "slot": hold["slot"],
        "price": hold["quoted_price"],
    }
    approval = request_approval(
        db=db,
        user_id=booking.user_id,
        conversation_id=conversation.id,
        action="book",
        details=details,
    )
    state = ConversationState(
        conversation_id=conversation.id,
        user_id=booking.user_id,
        status=AgentState.AWAITING_APPROVAL,
        errand={
            "service": booking.service,
            "area": cancelled_provider["area"],
            "provider_id": provider["id"],
            "selected_slot": hold["slot"],
            "budget": budget,
        },
        hold_id=hold["hold_id"],
        pending_action=PendingAction(
            action="book",
            details=details,
            approval_id=approval["approval_id"],
        ),
        metadata={
            "idempotency_key": str(uuid.uuid4()),
            "awaiting": "approval",
            "proactive_replacement_for": booking.id,
        },
    )
    tracer.log_event(
        user_id=booking.user_id,
        conversation_id=conversation.id,
        event_type="event",
        event_name="provider_cancelled_replacement_proposed",
        arguments={"booking_id": booking.id},
        result={"provider_id": provider["id"], "slot": slot},
    )
    from app.agent.orchestrator import orchestrator

    orchestrator._save_state(conversation, state)
    return {
        "status": "replacement_awaiting_approval",
        "booking_id": booking.id,
        "conversation_id": conversation.id,
        "replacement": {
            "provider_id": provider["id"],
            "provider_name": provider["name"],
            "slot": hold["slot"],
            "price": hold["quoted_price"],
            "expires_at": hold["expires_at"],
        },
        "message": (
            f"Your provider cancelled booking {booking.id}. I found "
            f"{provider['name']} for {hold['slot']} at ₹{hold['quoted_price']}. "
            "Please reply YES to approve this replacement or NO to decline."
        ),
    }


def _slot_matches_preference(
    slot: str,
    preference: str,
    earliest_time: str | None,
) -> bool:
    if "morning" in preference and not 5 <= int(slot[11:13]) < 12:
        return False
    if "afternoon" in preference and not 12 <= int(slot[11:13]) < 17:
        return False
    if "evening" in preference and not 17 <= int(slot[11:13]) < 23:
        return False

    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?",
        preference,
        re.IGNORECASE,
    )
    threshold_text = match or earliest_time
    if not threshold_text:
        return True

    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or "0")
        ampm = (match.group(3) or "").replace(".", "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    else:
        hour_text, minute_text = threshold_text.split(":")
        hour, minute = int(hour_text), int(minute_text)
    threshold = hour * 60 + minute
    slot_minutes = int(slot[11:13]) * 60 + int(slot[14:16])
    if any(word in preference for word in ("before", "earlier", "not after")):
        return slot_minutes < threshold
    return slot_minutes >= threshold
