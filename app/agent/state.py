
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AgentState(str, Enum):
    IDLE = "idle"
    COLLECTING_DETAILS = "collecting_details"
    SEARCHING_PROVIDERS = "searching_providers"
    COMPARING_PROVIDERS = "comparing_providers"
    AWAITING_PROVIDER_SELECTION = "awaiting_provider_selection"
    AWAITING_APPROVAL = "awaiting_approval"
    HOLDING_SLOT = "holding_slot"
    BOOKING = "booking"
    VERIFYING_BOOKING = "verifying_booking"
    BOOKED = "booked"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrandDetails(BaseModel):
    service: Optional[str] = None
    area: Optional[str] = None
    date_range: Optional[str] = None
    preferred_time: Optional[str] = None
    provider_id: Optional[str] = None
    selected_slot: Optional[str] = None
    budget: Optional[float] = None

    def missing_fields(self) -> list[str]:
        required = ["service", "area", "date_range"]
        return [
            field
            for field in required
            if not getattr(self, field)
        ]


class PendingAction(BaseModel):
    action: str
    details: dict[str, Any] = Field(default_factory=dict)
    approval_id: Optional[str] = None
    approved: bool = False


class ConversationState(BaseModel):
    conversation_id: str
    user_id: str
    status: AgentState = AgentState.IDLE
    errand: ErrandDetails = Field(default_factory=ErrandDetails)
    providers: list[dict[str, Any]] = Field(default_factory=list)
    pending_action: Optional[PendingAction] = None
    hold_id: Optional[str] = None
    booking_id: Optional[str] = None
    messages: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
