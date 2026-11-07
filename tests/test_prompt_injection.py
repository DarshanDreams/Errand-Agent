from app.tools.provider_tools import get_provider, PROVIDERS
from app.services.approval_service import (
    request_approval,
    verify_approval,
)
from app.database.connection import SessionLocal, init_db
from app.database.models import Approval, Conversation


USER_ID = "prompt-injection-test-user"
CONVERSATION_ID = "prompt-injection-test-conversation"


def test_malicious_review_is_untrusted_data():
    provider = get_provider("PROV003")

    malicious_review = next(
        review for review in provider["reviews"]
        if "IGNORE ALL PREVIOUS INSTRUCTIONS" in review
    )

    # Review content must not alter trusted provider data.
    assert provider["price"] == 1450
    assert "free" in malicious_review.lower()
    assert provider["id"] == "PROV003"

    print("PASS: Malicious review does not change provider price.")


def test_review_retrieval_does_not_create_booking():
    provider = get_provider("PROV003")

    assert provider["id"] == "PROV003"

    db = SessionLocal()

    try:
        booking_count_before = db.query(
            __import__(
                "app.database.models",
                fromlist=["Booking"]
            ).Booking
        ).count()

        # Retrieving untrusted review data must not create a booking.
        _ = provider["reviews"]

        booking_count_after = db.query(
            __import__(
                "app.database.models",
                fromlist=["Booking"]
            ).Booking
        ).count()

        assert booking_count_after == booking_count_before

        print("PASS: Reading malicious review creates no booking.")

    finally:
        db.close()


def test_malicious_review_cannot_bypass_approval():
    db = SessionLocal()

    try:
        conversation = db.query(Conversation).filter_by(
            id=CONVERSATION_ID
        ).first()

        if not conversation:
            conversation = Conversation(
                id=CONVERSATION_ID,
                user_id=USER_ID,
                state="idle",
                context={},
            )
            db.add(conversation)
            db.commit()

        details = {
            "provider_id": "PROV003",
            "slot": "2026-11-09 09:00",
            "price": 1450,
        }

        approval = request_approval(
            db,
            USER_ID,
            CONVERSATION_ID,
            "book",
            details,
        )

        verification = verify_approval(
            db,
            approval["approval_id"],
            USER_ID,
            details,
            expected_action="book",
        )

        assert verification["approved"] is False
        assert verification["reason"] == "user_approval_required"

        print("PASS: Explicit user approval is still required.")

    finally:
        db.query(Approval).filter_by(
            user_id=USER_ID
        ).delete(synchronize_session=False)

        db.query(Conversation).filter_by(
            id=CONVERSATION_ID
        ).delete(synchronize_session=False)

        db.commit()
        db.close()
