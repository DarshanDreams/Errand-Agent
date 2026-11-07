from datetime import datetime, timedelta, timezone

from app.database.connection import init_db, SessionLocal

from app.database.models import (
    Booking,
    SlotHold,
    IdempotencyRecord,
    Approval,
    Conversation
)

from app.tools.booking_tools import (
    hold_slot,
    book,
    cancel,
    reschedule
)

from app.tools.mock_scenarios import scenario_manager

from app.services.approval_service import (
    request_approval,
    approve_action
)


PROVIDER_ID = "PROV001"
USER_ID = "demo-user"

CANCEL_KEY = "cancel-operation-001"
RESCHEDULE_CONFLICT_KEY = "reschedule-conflict-001"
RESCHEDULE_SUCCESS_KEY = "reschedule-success-001"


def clean_test_data(keys, slots, conversation_id):
    db = SessionLocal()

    try:
        for key in keys:
            db.query(Booking).filter_by(
                idempotency_key=key
            ).delete(synchronize_session=False)

            db.query(IdempotencyRecord).filter_by(
                idempotency_key=key
            ).delete(synchronize_session=False)

        operation_keys = [
            CANCEL_KEY,
            RESCHEDULE_CONFLICT_KEY,
            RESCHEDULE_SUCCESS_KEY
        ]

        for operation_key in operation_keys:
            db.query(IdempotencyRecord).filter_by(
                idempotency_key=operation_key
            ).delete(synchronize_session=False)

        db.query(Approval).filter_by(
            user_id=USER_ID
        ).delete(synchronize_session=False)

        db.query(Conversation).filter_by(
            id=conversation_id
        ).delete(synchronize_session=False)

        for slot in slots:
            db.query(SlotHold).filter_by(
                provider_id=PROVIDER_ID,
                slot=slot
            ).delete(synchronize_session=False)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def create_approved_booking(key, slot, conversation_id):
    hold = hold_slot(PROVIDER_ID, slot)

    assert "hold_id" in hold, hold

    db = SessionLocal()

    try:
        conversation = Conversation(
            id=conversation_id,
            user_id=USER_ID,
            state="idle",
            context={}
        )

        db.add(conversation)
        db.commit()

        details = {
            "provider_id": hold["provider_id"],
            "slot": hold["slot"],
            "price": hold["quoted_price"]
        }

        approval = request_approval(
            db=db,
            user_id=USER_ID,
            conversation_id=conversation_id,
            action="book_service",
            details=details
        )

        approved = approve_action(
            db=db,
            approval_id=approval["approval_id"],
            user_id=USER_ID,
            current_details=details
        )

        assert approved["status"] == "approved"

    finally:
        db.close()

    result = book(
        hold_id=hold["hold_id"],
        idempotency_key=key,
        approval_id=approval["approval_id"]
    )

    assert result["status"] == "confirmed", result

    return result["booking_id"]


def create_operation_approval(
    booking_id,
    conversation_id,
    action,
    operation_details
):
    db = SessionLocal()

    try:
        booking = db.query(Booking).filter_by(
            id=booking_id
        ).first()

        assert booking is not None, "Booking not found"

        if action == "cancel":
            details = {
                "booking_id": booking.id,
                "provider_id": booking.provider_id,
                "service": booking.service,
                "slot": booking.slot
            }

        elif action == "reschedule":
            details = {
                "booking_id": booking.id,
                "provider_id": booking.provider_id,
                "old_slot": booking.slot,
                "new_slot": operation_details["new_slot"]
            }

        else:
            raise ValueError(f"Unsupported action: {action}")

        approval = request_approval(
            db=db,
            user_id=USER_ID,
            conversation_id=conversation_id,
            action=action,
            details=details
        )

        result = approve_action(
            db=db,
            approval_id=approval["approval_id"],
            user_id=USER_ID,
            current_details=details
        )

        assert result["status"] == "approved", result

        return approval["approval_id"]

    finally:
        db.close()


def test_cancellation():
    key = "cancel-test-booking"
    slot = "2026-11-09 09:00"
    conversation_id = "cancel-test-conversation"

    clean_test_data([key], [slot], conversation_id)

    scenario_manager.config.booking_outcome = "success"
    scenario_manager.config.cancellation_failures_remaining = 1

    booking_id = create_approved_booking(
        key,
        slot,
        conversation_id
    )

    approval_id = create_operation_approval(
        booking_id=booking_id,
        conversation_id=conversation_id,
        action="cancel",
        operation_details={}
    )

    # First attempt should return a simulated 503.
    first = cancel(
        booking_id=booking_id,
        idempotency_key=CANCEL_KEY,
        approval_id=approval_id
    )

    print("Cancellation first attempt:", first)

    assert first.get("error") == "service_unavailable", first
    assert first.get("status_code") == 503, first

    print("PASS: First cancellation returned 503")

    # Retry should succeed using the same approval and idempotency key.
    second = cancel(
        booking_id=booking_id,
        idempotency_key=CANCEL_KEY,
        approval_id=approval_id
    )

    assert second["status"] == "cancelled", second

    print("PASS: Cancellation retry succeeded")

    # Repeated cancellation must remain safe.
    third = cancel(
        booking_id=booking_id,
        idempotency_key=CANCEL_KEY,
        approval_id=approval_id
    )

    assert third["status"] == "cancelled", third

    print("PASS: Repeated cancellation handled safely")


def test_rescheduling():
    key = "reschedule-test-booking"
    original_slot = "2026-11-09 09:00"

    conflict_slot = "2026-11-09 10:00"
    new_slot = "2026-11-10 11:00"

    conversation_id = "reschedule-test-conversation"

    clean_test_data(
        [key],
        [original_slot],
        conversation_id
    )

    scenario_manager.config.booking_outcome = "success"
    scenario_manager.config.conflicting_slots = [
        conflict_slot
    ]

    booking_id = create_approved_booking(
        key,
        original_slot,
        conversation_id
    )

    # Approval for the first requested new slot.
    conflict_approval_id = create_operation_approval(
        booking_id=booking_id,
        conversation_id=conversation_id,
        action="reschedule",
        operation_details={"new_slot": conflict_slot}
    )

    conflict = reschedule(
        booking_id=booking_id,
        slot=conflict_slot,
        idempotency_key=RESCHEDULE_CONFLICT_KEY,
        approval_id=conflict_approval_id
    )

    assert conflict["error"] == "slot_conflict", conflict

    print("PASS: Slot conflict detected")

    # The replacement slot is a different action detail.
    # It therefore needs a fresh approval.
    success_approval_id = create_operation_approval(
        booking_id=booking_id,
        conversation_id=conversation_id,
        action="reschedule",
        operation_details={"new_slot": new_slot}
    )

    result = reschedule(
        booking_id=booking_id,
        slot=new_slot,
        idempotency_key=RESCHEDULE_SUCCESS_KEY,
        approval_id=success_approval_id
    )

    assert result["status"] == "rescheduled", result
    assert result["new_slot"] == new_slot

    print("PASS: Booking rescheduled successfully")


def test_expired_hold():
    key = "expired-hold-test"
    slot = "2026-11-09 10:00"

    conversation_id = "expired-hold-test-conversation"

    clean_test_data(
        [key],
        [slot],
        conversation_id
    )

    scenario_manager.config.booking_outcome = "success"
    scenario_manager.config.force_hold_expired = False

    hold = hold_slot(PROVIDER_ID, slot)

    assert "hold_id" in hold, hold

    db = SessionLocal()

    try:
        stored_hold = db.query(SlotHold).filter_by(
            id=hold["hold_id"]
        ).first()

        stored_hold.expires_at = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        )

        db.commit()

    finally:
        db.close()

    result = book(
        hold_id=hold["hold_id"],
        idempotency_key=key
    )

    assert result["error"] == "hold_expired", result

    print("PASS: Expired hold rejected")


if __name__ == "__main__":
    init_db()

    try:
        test_cancellation()
        test_rescheduling()
        test_expired_hold()

        print("\nALL CANCELLATION AND RESCHEDULING TESTS PASSED!")

    finally:
        scenario_manager.config.booking_outcome = None
        scenario_manager.config.force_hold_expired = False
        scenario_manager.config.conflicting_slots = []
        scenario_manager.config.cancellation_failures_remaining = 1