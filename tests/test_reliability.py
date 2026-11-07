
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


def clean_data(keys, slots, conversation_ids=None):
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

        for conversation_id in conversation_ids or []:
            db.query(Conversation).filter_by(
                id=conversation_id
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


def create_approved_action(hold, conversation_id):
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


def test_price_change():
    key = "price-test-001"
    slot = "2026-11-09 09:00"

    clean_data([key], [slot])

    scenario_manager.config.quoted_price_override = 1200

    hold = hold_slot("PROV001", slot)
    assert "hold_id" in hold, hold

    original_price = hold["quoted_price"]

    scenario_manager.config.quoted_price_override = 1500

    assert original_price == 1200

    print("PASS: Original quoted price persisted")

    scenario_manager.config.quoted_price_override = None


def test_idempotency_key_reuse():
    key = "idempotency-reuse-001"

    slot1 = "2026-11-09 09:00"
    slot2 = "2026-11-09 10:00"

    conversation_id = "reliability-idempotency-conversation"

    clean_data(
        [key],
        [slot1, slot2],
        [conversation_id]
    )

    scenario_manager.config.booking_outcome = "success"

    hold1 = hold_slot("PROV001", slot1)
    hold2 = hold_slot("PROV001", slot2)

    assert "hold_id" in hold1, hold1
    assert "hold_id" in hold2, hold2

    approval_id = create_approved_action(
        hold1,
        conversation_id
    )

    first = book(
        hold_id=hold1["hold_id"],
        idempotency_key=key,
        approval_id=approval_id
    )

    assert first["status"] == "confirmed", first

    # Reuse the same idempotency key for another hold.
    second = book(
        hold_id=hold2["hold_id"],
        idempotency_key=key,
        approval_id=approval_id
    )

    assert second["error"] == (
        "idempotency_key_reused_with_different_request"
    )

    print("PASS: Idempotency key reuse rejected")


def test_database_persistence():
    key = "persistence-test-001"
    slot = "2026-11-10 11:00"

    conversation_id = "reliability-persistence-conversation"

    clean_data(
        [key],
        [slot],
        [conversation_id]
    )

    scenario_manager.config.booking_outcome = "success"

    hold = hold_slot("PROV001", slot)
    assert "hold_id" in hold, hold

    approval_id = create_approved_action(
        hold,
        conversation_id
    )

    result = book(
        hold_id=hold["hold_id"],
        idempotency_key=key,
        approval_id=approval_id
    )

    assert result["status"] == "confirmed", result

    # Close the current database session.
    db = SessionLocal()
    db.close()

    # Retrieve the booking using a fresh session.
    status = get_booking_status(
        idempotency_key=key
    )

    assert status["status"] == "confirmed", status

    print("PASS: Booking persisted across database sessions")


if __name__ == "__main__":
    init_db()

    try:
        test_price_change()
        test_idempotency_key_reuse()
        test_database_persistence()

        print("\nALL RELIABILITY TESTS PASSED!")

    finally:
        scenario_manager.config.booking_outcome = None
        scenario_manager.config.quoted_price_override = None
