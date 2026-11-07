
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
    get_booking_status
)

from app.tools.mock_scenarios import scenario_manager

from app.services.approval_service import (
    request_approval,
    approve_action
)


USER_ID = "demo-user"
CONVERSATION_ID = "timeout-test-conversation"


def clean_test_data(keys, slots):
    db = SessionLocal()

    try:
        for key in keys:
            db.query(Booking).filter_by(
                idempotency_key=key
            ).delete(synchronize_session=False)

            db.query(IdempotencyRecord).filter_by(
                idempotency_key=key
            ).delete(synchronize_session=False)

        db.query(Approval).filter_by(
            user_id=USER_ID
        ).delete(synchronize_session=False)

        db.query(Conversation).filter_by(
            id=CONVERSATION_ID
        ).delete(synchronize_session=False)

        for slot in slots:
            db.query(SlotHold).filter_by(
                provider_id="PROV001",
                slot=slot
            ).delete(synchronize_session=False)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def create_approved_action(hold):
    db = SessionLocal()

    try:
        conversation = Conversation(
            id=CONVERSATION_ID,
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
            conversation_id=CONVERSATION_ID,
            action="book_service",
            details=details
        )

        result = approve_action(
            db=db,
            approval_id=approval["approval_id"],
            user_id=USER_ID,
            current_details=details
        )

        assert result["status"] == "approved"

        return approval["approval_id"]

    finally:
        db.close()


def test_timeout_after_success():
    key = "timeout-success-001"
    slot = "2026-11-09 09:00"

    clean_test_data([key], [slot])

    scenario_manager.config.booking_outcome = "timeout"
    scenario_manager.config.timeout_booking_succeeds = True

    hold = hold_slot("PROV001", slot)
    assert "hold_id" in hold, hold

    approval_id = create_approved_action(hold)

    result = book(
        hold_id=hold["hold_id"],
        idempotency_key=key,
        approval_id=approval_id
    )

    assert result["error"] == "timeout"

    print("PASS: Booking request timed out")

    # Check the authoritative booking status.
    status = get_booking_status(
        idempotency_key=key
    )

    assert status["status"] == "confirmed", status

    print("PASS: Booking found despite timeout")

    # Retry with the same idempotency key.
    retry = book(
        hold_id=hold["hold_id"],
        idempotency_key=key,
        approval_id=approval_id
    )

    assert retry["status"] == "confirmed"
    assert retry["booking_id"] == status["booking_id"]

    print("PASS: Retry returned existing booking")
    print("\nTIMEOUT-AFTER-SUCCESS TEST PASSED!")


def test_timeout_without_success():
    key = "timeout-failure-001"
    slot = "2026-11-10 11:00"

    clean_test_data([key], [slot])

    scenario_manager.config.booking_outcome = "timeout"
    scenario_manager.config.timeout_booking_succeeds = False

    hold = hold_slot("PROV001", slot)
    assert "hold_id" in hold, hold

    approval_id = create_approved_action(hold)

    result = book(
        hold_id=hold["hold_id"],
        idempotency_key=key,
        approval_id=approval_id
    )

    assert result["error"] == "timeout"

    print("PASS: Booking request timed out")

    status = get_booking_status(
        idempotency_key=key
    )

    assert status["status"] == "not_booked", status

    print("PASS: No booking exists")
    print("\nTIMEOUT-WITHOUT-SUCCESS TEST PASSED!")


if __name__ == "__main__":
    init_db()

    try:
        test_timeout_after_success()
        test_timeout_without_success()

        print("\nALL TIMEOUT RECOVERY TESTS PASSED!")

    finally:
        scenario_manager.config.booking_outcome = None
        scenario_manager.config.timeout_booking_succeeds = None
