
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
    get_booking_status,
    list_bookings
)

from app.services.approval_service import request_approval

from app.tools.mock_scenarios import scenario_manager


TEST_PROVIDER = "PROV001"
TEST_SLOT = "2026-11-09 09:00"
TEST_IDEMPOTENCY_KEY = "test-booking-001"

TEST_USER_ID = "demo-user"
TEST_CONVERSATION_ID = "booking-test-conversation"


def clean_test_data():
    db = SessionLocal()

    try:
        db.query(Booking).filter_by(
            idempotency_key=TEST_IDEMPOTENCY_KEY
        ).delete(synchronize_session=False)

        db.query(IdempotencyRecord).filter_by(
            idempotency_key=TEST_IDEMPOTENCY_KEY
        ).delete(synchronize_session=False)

        db.query(Approval).filter_by(
            user_id=TEST_USER_ID
        ).delete(synchronize_session=False)

        db.query(Conversation).filter_by(
            id=TEST_CONVERSATION_ID
        ).delete(synchronize_session=False)

        db.query(SlotHold).filter_by(
            provider_id=TEST_PROVIDER,
            slot=TEST_SLOT
        ).delete(synchronize_session=False)

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def create_test_conversation():
    db = SessionLocal()

    try:
        conversation = Conversation(
            id=TEST_CONVERSATION_ID,
            user_id=TEST_USER_ID,
            state="idle",
            context={}
        )

        db.add(conversation)
        db.commit()

    finally:
        db.close()


def test_booking_workflow():

    init_db()
    clean_test_data()

    try:
        create_test_conversation()

        scenario_manager.config.booking_outcome = "success"

        # 1. Hold a slot.
        hold = hold_slot(
            provider_id=TEST_PROVIDER,
            slot=TEST_SLOT
        )

        assert "hold_id" in hold, hold
        assert hold["status"] == "held"

        print("PASS: Slot held successfully")

        # 2. Request approval for the exact hold details.
        db = SessionLocal()

        try:
            approval = request_approval(
                db=db,
                user_id=TEST_USER_ID,
                conversation_id=TEST_CONVERSATION_ID,
                action="book_service",
                details={
                    "provider_id": hold["provider_id"],
                    "slot": hold["slot"],
                    "price": hold["quoted_price"]
                }
            )

        finally:
            db.close()

        # 3. Explicitly approve the requested action.
        from app.services.approval_service import approve_action

        db = SessionLocal()

        try:
            approval_result = approve_action(
                db=db,
                approval_id=approval["approval_id"],
                user_id=TEST_USER_ID,
                current_details={
                    "provider_id": hold["provider_id"],
                    "slot": hold["slot"],
                    "price": hold["quoted_price"]
                }
            )

            assert approval_result["status"] == "approved"

        finally:
            db.close()

        print("PASS: User approval recorded")

        # 4. Book with the approval ID.
        result = book(
            hold_id=hold["hold_id"],
            idempotency_key=TEST_IDEMPOTENCY_KEY,
            approval_id=approval["approval_id"]
        )

        assert result["status"] == "confirmed", result

        print("PASS: Booking confirmed with approval")

        # 5. Verify booking status.
        status = get_booking_status(
            idempotency_key=TEST_IDEMPOTENCY_KEY
        )

        assert status["status"] == "confirmed"

        print("PASS: Booking status verified")

        # 6. Repeat the same booking request.
        duplicate = book(
            hold_id=hold["hold_id"],
            idempotency_key=TEST_IDEMPOTENCY_KEY,
            approval_id=approval["approval_id"]
        )

        assert duplicate["booking_id"] == result["booking_id"]

        print("PASS: Duplicate booking prevented")

        # 7. Verify booking history.
        bookings = list_bookings(TEST_USER_ID)

        matching = [
            item for item in bookings
            if item["booking_id"] == result["booking_id"]
        ]

        assert len(matching) == 1

        print("PASS: Booking appears exactly once in history")

        print("\nALL BOOKING WORKFLOW TESTS PASSED!")

    finally:
        scenario_manager.config.booking_outcome = None


if __name__ == "__main__":
    test_booking_workflow()
