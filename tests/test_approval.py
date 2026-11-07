from app.database.connection import init_db, SessionLocal
from app.database.models import Approval, Conversation

from app.services.approval_service import (
    request_approval,
    approve_action,
    verify_approval
)


USER_ID = "approval-test-user"
CONVERSATION_ID = "approval-test-conversation"

DETAILS = {
    "provider_id": "PROV001",
    "slot": "2026-11-10 11:00",
    "price": 1200
}


def clean_test_data():
    db = SessionLocal()

    try:
        db.query(Approval).filter_by(
            user_id=USER_ID
        ).delete(synchronize_session=False)

        db.query(Conversation).filter_by(
            id=CONVERSATION_ID
        ).delete(synchronize_session=False)

        db.commit()

    finally:
        db.close()


def create_test_conversation(db):
    conversation = db.query(Conversation).filter_by(
        id=CONVERSATION_ID
    ).first()

    if not conversation:
        conversation = Conversation(
            id=CONVERSATION_ID,
            user_id=USER_ID,
            state="idle",
            context={}
        )

        db.add(conversation)
        db.commit()


def test_approval_required():
    db = SessionLocal()

    try:
        create_test_conversation(db)

        result = request_approval(
            db,
            USER_ID,
            CONVERSATION_ID,
            "book_service",
            DETAILS
        )

        verification = verify_approval(
            db,
            result["approval_id"],
            USER_ID,
            DETAILS
        )

        assert verification["approved"] is False
        assert verification["reason"] == "user_approval_required"

        print("PASS: Booking blocked before user approval")

    finally:
        db.close()


def test_exact_details_approved():
    db = SessionLocal()

    try:
        create_test_conversation(db)

        result = request_approval(
            db,
            USER_ID,
            CONVERSATION_ID,
            "book_service",
            DETAILS
        )

        approval_id = result["approval_id"]

        approved = approve_action(
            db,
            approval_id,
            USER_ID,
            DETAILS
        )

        assert approved["status"] == "approved"

        verification = verify_approval(
            db,
            approval_id,
            USER_ID,
            DETAILS
        )

        assert verification["approved"] is True

        print("PASS: Exact approved details accepted")

    finally:
        db.close()


def test_changed_price_rejected():
    db = SessionLocal()

    try:
        create_test_conversation(db)

        result = request_approval(
            db,
            USER_ID,
            CONVERSATION_ID,
            "book_service",
            DETAILS
        )

        changed_details = {
            **DETAILS,
            "price": 1500
        }

        result = approve_action(
            db,
            result["approval_id"],
            USER_ID,
            changed_details
        )

        assert result["error"] == "approval_details_changed"

        print("PASS: Changed price requires new approval")

    finally:
        db.close()


def test_changed_slot_rejected():
    db = SessionLocal()

    try:
        create_test_conversation(db)

        result = request_approval(
            db,
            USER_ID,
            CONVERSATION_ID,
            "book_service",
            DETAILS
        )

        changed_details = {
            **DETAILS,
            "slot": "2026-11-10 12:00"
        }

        result = approve_action(
            db,
            result["approval_id"],
            USER_ID,
            changed_details
        )

        assert result["error"] == "approval_details_changed"

        print("PASS: Changed slot requires new approval")

    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    clean_test_data()

    try:
        test_approval_required()
        test_exact_details_approved()
        test_changed_price_rejected()
        test_changed_slot_rejected()

        print("\nALL APPROVAL TESTS PASSED!")

    finally:
        clean_test_data()