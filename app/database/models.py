import uuid

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Index,
    UniqueConstraint
)

from app.database.connection import Base


def generate_id():
    return str(uuid.uuid4())


def utc_now():
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=generate_id)
    user_id = Column(String, nullable=False, index=True)
    state = Column(String, default="idle", nullable=False)
    context = Column(JSON, default=dict, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(String, primary_key=True, default=generate_id)
    user_id = Column(String, nullable=False, index=True)
    provider_id = Column(String, nullable=False)
    service = Column(String, nullable=False)
    slot = Column(JSON, nullable=False)
    price = Column(Float, nullable=False)
    status = Column(String, default="pending", nullable=False)
    hold_id = Column(String, nullable=True, unique=True)
    idempotency_key = Column(String, nullable=False, unique=True)

    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(String, primary_key=True, default=generate_id)
    user_id = Column(String, nullable=False, index=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    action = Column(String, nullable=False)
    details = Column(JSON, nullable=False)
    fingerprint = Column(String, nullable=False)
    approved = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    approved_at = Column(DateTime(timezone=True), nullable=True)


class Memory(Base):
    __tablename__ = "memories"

    id = Column(String, primary_key=True, default=generate_id)
    user_id = Column(String, nullable=False, index=True)
    key = Column(String, nullable=False)
    value = Column(JSON, nullable=False)
    source = Column(String, default="explicit")
    confidence = Column(Float, default=1.0)
    active = Column(Boolean, default=True)
    forgotten_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_memory_key"),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id = Column(String, primary_key=True, default=generate_id)
    idempotency_key = Column(String, unique=True, nullable=False)
    operation = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    status = Column(String, default="started")
    result = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Trace(Base):
    __tablename__ = "traces"

    id = Column(String, primary_key=True, default=generate_id)
    conversation_id = Column(String, nullable=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    event_name = Column(String, nullable=False)
    arguments = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    latency_ms = Column(Float, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    estimated_cost = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now)


class SlotHold(Base):
    __tablename__ = "slot_holds"

    id = Column(String, primary_key=True, default=generate_id)
    provider_id = Column(String, nullable=False)
    slot = Column(String, nullable=False)
    quoted_price = Column(Float, nullable=False)
    status = Column(String, default="held", nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now)


Index(
    "ix_trace_conversation_created",
    Trace.conversation_id,
    Trace.created_at
)