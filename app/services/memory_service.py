
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import Memory


class MemoryService:
    def __init__(self, db: Session):
        self.db = db

    def save_memory(
        self,
        user_id: str,
        key: str,
        value: Any,
        source: str = "explicit",
        confidence: float = 1.0,
    ) -> Memory:
        """Create or update a user's preference."""

        memory = (
            self.db.query(Memory)
            .filter(
                Memory.user_id == user_id,
                Memory.key == key,
            )
            .first()
        )

        if memory is None:
            memory = Memory(
                user_id=user_id,
                key=key,
                value=value,
                source=source,
                confidence=confidence,
                active=True,
                forgotten_at=None,
            )
            self.db.add(memory)
        else:
            memory.value = value
            memory.source = source
            memory.confidence = confidence
            memory.active = True
            memory.forgotten_at = None

        self.db.commit()
        self.db.refresh(memory)
        return memory

    def get_memories(
        self,
        user_id: str,
        include_inferred: bool = False,
    ) -> list[dict]:
        """Return active memories, excluding inferred facts by default."""

        query = self.db.query(Memory).filter(
            Memory.user_id == user_id,
            Memory.active.is_(True),
        )

        if not include_inferred:
            query = query.filter(Memory.source == "explicit")

        memories = query.order_by(Memory.updated_at.desc()).all()

        return [
            {
                "key": memory.key,
                "value": memory.value,
                "source": memory.source,
                "confidence": memory.confidence,
            }
            for memory in memories
        ]

    def get_relevant_memories(
        self,
        user_id: str,
        request_text: str,
        limit: int = 10,
    ) -> list[dict]:
        """Return only explicit, active preferences relevant to this request."""
        relevant_keys = {
            "preferred_area",
            "preferred_service",
            "preferred_time",
            "earliest_acceptable_time",
            "preferred_provider",
            "preferred_technician",
            "maximum_budget",
            "budget",
        }
        text = request_text.casefold()
        mentioned = {
            key for key in relevant_keys
            if key.replace("_", " ") in text
        }

        if any(word in text for word in ("same", "last time", "technician")):
            mentioned.update({"preferred_provider", "preferred_technician"})
        if any(word in text for word in ("morning", "before", "after", "time", "slot")):
            mentioned.update({"preferred_time", "earliest_acceptable_time"})
        if any(word in text for word in ("under", "budget", "price", "cost", "₹")):
            mentioned.update({"maximum_budget", "budget"})
        if any(word in text for word in (
            "find", "book", "service", "plumber", "plumbing",
            "electrician", "ac", "repair", "servicing",
        )):
            mentioned.update({"preferred_area", "preferred_service"})

        return [
            memory for memory in self.get_memories(user_id)
            if memory["key"] in relevant_keys and memory["key"] in mentioned
        ][:limit]

    def get_forgotten_values(self, user_id: str) -> list[str]:
        """Return historical values that must be excluded from model context."""
        forgotten = (
            self.db.query(Memory.value)
            .filter(
                Memory.user_id == user_id,
                Memory.active.is_(False),
            )
            .all()
        )
        return [
            str(row[0]).casefold()
            for row in forgotten
            if isinstance(row[0], (str, int, float)) and str(row[0]).strip()
        ]

    def correct_memory(
        self,
        user_id: str,
        key: str,
        new_value: Any,
    ) -> Memory:
        """Correct an existing preference."""

        memory = (
            self.db.query(Memory)
            .filter(
                Memory.user_id == user_id,
                Memory.key == key,
                Memory.active.is_(True),
            )
            .first()
        )

        if memory is None:
            raise ValueError(f"Active memory '{key}' was not found.")

        memory.value = new_value
        memory.source = "explicit"
        memory.confidence = 1.0

        self.db.commit()
        self.db.refresh(memory)
        return memory

    def forget_memory(self, user_id: str, key: str) -> bool:
        """Deactivate a preference without deleting its audit history."""

        memory = (
            self.db.query(Memory)
            .filter(
                Memory.user_id == user_id,
                Memory.key == key,
                Memory.active.is_(True),
            )
            .first()
        )

        if memory is None:
            return False

        from app.database.models import utc_now

        memory.active = False
        memory.forgotten_at = utc_now()

        self.db.commit()
        return True
