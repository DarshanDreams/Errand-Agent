import uuid
import pytest

from app.database.connection import SessionLocal, init_db
from app.database.models import (
    Booking,
    SlotHold,
    Approval,
    Conversation,
    IdempotencyRecord,
)
from app.agent.orchestrator import AgentOrchestrator
from app.agent.state import (
    AgentState,
    ConversationState,
    PendingAction,
)
from app.services.approval_service import request_approval
from app.tools.booking_tools import hold_slot
from app.tools.mock_scenarios import scenario_manager


@pytest.mark.asyncio
async def test_booking_completes_after_restart():
    init_db()

    user_id = "demo-user"
    conversation_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())

    provider_id = "PROV006"
    slot = "2026-11-12 10:00"

    hold_id = None
    previous_outcome = scenario_manager.config.booking_outcome

    scenario_manager.config.booking_outcome = "success"

    try:
        # Create the hold.
        hold = hold_slot(provider_id, slot)
        assert "hold_id" in hold, hold

        hold_id = hold["hold_id"]

        details = {
            "provider_id": hold["provider_id"],
            "slot": hold["slot"],
            "price": hold["quoted_price"],
        }

        # First session: save approval and pending state.
        db1 = SessionLocal()

        conversation = Conversation(
            id=conversation_id,
            user_id=user_id,
            state=AgentState.AWAITING_APPROVAL.value,
            context={},
        )

        db1.add(conversation)
        db1.commit()

        approval = request_approval(
            db=db1,
            user_id=user_id,
            conversation_id=conversation_id,
            action="book_service",
            details=details,
        )

        state = ConversationState(
            conversation_id=conversation_id,
            user_id=user_id,
            status=AgentState.AWAITING_APPROVAL,
            hold_id=hold_id,
            pending_action=PendingAction(
                action="book",
                details=details,
                approval_id=approval["approval_id"],
                approved=False,
            ),
            metadata={
                "idempotency_key": idempotency_key,
                "awaiting": "approval",
            },
        )

        AgentOrchestrator()._save_state(conversation, state)
        db1.close()

        # Simulated restart: new orchestrator and DB session.
        orchestrator = AgentOrchestrator()
        db2 = SessionLocal()

        try:
            response = await orchestrator.handle_message(
                db=db2,
                user_message="YES",
                conversation_id=conversation_id,
                user_id=user_id,
            )

            assert response["state"] == "booked", response
            assert response["booking_id"], response

            booking = db2.query(Booking).filter_by(
                idempotency_key=idempotency_key
            ).first()

            assert booking is not None
            assert booking.status == "confirmed"
            assert booking.provider_id == provider_id
            assert booking.id == response["booking_id"]

            count = db2.query(Booking).filter_by(
                idempotency_key=idempotency_key
            ).count()

            assert count == 1

            print("PASS: Booking completed after restart.")
            print("PASS: Approval restored and validated.")
            print("PASS: Original idempotency key reused.")
            print("PASS: Exactly one confirmed booking created.")

        finally:
            db2.close()

    finally:
        scenario_manager.config.booking_outcome = previous_outcome

        cleanup_db = SessionLocal()

        try:
            cleanup_db.query(Booking).filter_by(
                idempotency_key=idempotency_key
            ).delete(synchronize_session=False)

            cleanup_db.query(IdempotencyRecord).filter_by(
                idempotency_key=idempotency_key
            ).delete(synchronize_session=False)

            cleanup_db.query(Approval).filter_by(
                conversation_id=conversation_id
            ).delete(synchronize_session=False)

            cleanup_db.query(Conversation).filter_by(
                id=conversation_id
            ).delete(synchronize_session=False)

            if hold_id:
                cleanup_db.query(SlotHold).filter_by(
                    id=hold_id
                ).delete(synchronize_session=False)

            cleanup_db.commit()

        finally:
            cleanup_db.close()
