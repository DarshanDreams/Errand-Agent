import uuid

from app.database.connection import SessionLocal, init_db
from app.database.models import Memory
from app.services.memory_service import MemoryService


USER_ID = f"memory-test-{uuid.uuid4()}"


def cleanup(db):
    db.query(Memory).filter_by(user_id=USER_ID).delete(
        synchronize_session=False
    )
    db.commit()


def test_save_explicit_memory():
    db = SessionLocal()

    try:
        service = MemoryService(db)

        memory = service.save_memory(
            user_id=USER_ID,
            key="preferred_area",
            value="Andheri",
        )

        assert memory.value == "Andheri"
        assert memory.active is True
        assert memory.source == "explicit"

        print("PASS: Explicit memory saved.")

    finally:
        cleanup(db)
        db.close()


def test_correct_existing_memory():
    db = SessionLocal()

    try:
        service = MemoryService(db)

        service.save_memory(
            USER_ID, "preferred_area", "Andheri"
        )

        memory = service.correct_memory(
            USER_ID, "preferred_area", "Bandra"
        )

        assert memory.value == "Bandra"
        assert memory.source == "explicit"
        assert memory.confidence == 1.0

        print("PASS: Existing memory corrected.")

    finally:
        cleanup(db)
        db.close()


def test_forgotten_memory_excluded():
    db = SessionLocal()

    try:
        service = MemoryService(db)

        service.save_memory(
            USER_ID, "preferred_area", "Andheri"
        )

        result = service.forget_memory(
            USER_ID, "preferred_area"
        )

        assert result is True

        memories = service.get_memories(USER_ID)

        assert not any(
            item["key"] == "preferred_area"
            for item in memories
        )

        print("PASS: Forgotten memory excluded from context.")

    finally:
        cleanup(db)
        db.close()


def test_inferred_memory_excluded_by_default():
    db = SessionLocal()

    try:
        service = MemoryService(db)

        service.save_memory(
            USER_ID,
            "possible_budget",
            1500,
            source="inferred",
            confidence=0.6,
        )

        default_memories = service.get_memories(USER_ID)
        all_memories = service.get_memories(
            USER_ID,
            include_inferred=True,
        )

        assert not default_memories
        assert len(all_memories) == 1

        print("PASS: Inferred memory excluded by default.")

    finally:
        cleanup(db)
        db.close()


if __name__ == "__main__":
    init_db()
