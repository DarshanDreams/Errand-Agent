import uuid

from app.database.connection import SessionLocal, init_db
from app.database.models import Conversation
from app.agent.orchestrator import AgentOrchestrator
from app.agent.state import AgentState, ConversationState, PendingAction


def test_pending_approval_survives_restart():
    init_db()

    user_id = "restart-test-user"
    conversation_id = str(uuid.uuid4())

    approval_id = str(uuid.uuid4())
    hold_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())

    db1 = SessionLocal()

    try:
        conversation = Conversation(
            id=conversation_id,
            user_id=user_id,
            state=AgentState.AWAITING_APPROVAL.value,
            context={},
        )

        db1.add(conversation)
        db1.commit()

        orchestrator1 = AgentOrchestrator()

        state = ConversationState(
            conversation_id=conversation_id,
            user_id=user_id,
            status=AgentState.AWAITING_APPROVAL,
            hold_id=hold_id,
            pending_action=PendingAction(
                action="book",
                details={
                    "provider_id": "PROV001",
                    "slot": "2026-11-10 11:00",
                    "price": 1200,
                },
                approval_id=approval_id,
                approved=False,
            ),
            metadata={
                "idempotency_key": idempotency_key,
                "awaiting": "approval",
            },
        )

        orchestrator1._save_state(conversation, state)

    finally:
        db1.close()

    # Simulate application restart: new orchestrator and DB session.
    orchestrator2 = AgentOrchestrator()
    db2 = SessionLocal()

    try:
        restored_conversation = db2.query(Conversation).filter_by(
            id=conversation_id,
            user_id=user_id,
        ).first()

        assert restored_conversation is not None

        restored_state = orchestrator2._load_state(
            restored_conversation
        )

        assert restored_state.status == AgentState.AWAITING_APPROVAL
        assert restored_state.hold_id == hold_id
        assert restored_state.pending_action is not None
        assert restored_state.pending_action.approval_id == approval_id
        assert restored_state.metadata["idempotency_key"] == idempotency_key
        assert restored_state.pending_action.approved is False

        print("PASS: Pending approval survived simulated restart.")
        print("PASS: Hold ID restored.")
        print("PASS: Approval ID restored.")
        print("PASS: Idempotency key restored.")

    finally:
        db2.query(Conversation).filter_by(
            id=conversation_id
        ).delete(synchronize_session=False)
        db2.commit()
        db2.close()


if __name__ == "__main__":
    init_db()
