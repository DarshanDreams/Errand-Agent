import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import Approval


def normalize_action(action: Optional[str]) -> Optional[str]:
    """Map legacy action names to the canonical action names."""
    if action is None:
        return None

    aliases = {
        "book_service": "book",
        "booking": "book",
        "cancel_booking": "cancel",
        "reschedule_booking": "reschedule",
    }
    return aliases.get(action, action)


def create_approval_fingerprint(details: dict) -> str:
    """Create a stable fingerprint for the exact action details."""
    normalized_details = json.dumps(
        details,
        sort_keys=True,
        separators=(",", ":")
    )
    return hashlib.sha256(normalized_details.encode("utf-8")).hexdigest()


def request_approval(
    db: Session,
    user_id: str,
    conversation_id: str,
    action: str,
    details: dict
):
    """Create a pending approval for a specific action and its details."""
    fingerprint = create_approval_fingerprint(details)

    approval = Approval(
        user_id=user_id,
        conversation_id=conversation_id,
        action=action,
        details=details,
        fingerprint=fingerprint,
        approved=False
    )

    db.add(approval)
    db.commit()
    db.refresh(approval)

    return {
        "approval_id": approval.id,
        "status": "pending",
        "action": action,
        "details": details,
        "message": "Waiting for explicit user approval."
    }


def approve_action(
    db: Session,
    approval_id: str,
    user_id: str,
    current_details: dict,
    expected_action: Optional[str] = None
):
    """Record approval only if the action and details match."""
    approval = db.query(Approval).filter_by(
        id=approval_id,
        user_id=user_id
    ).first()

    if not approval:
        return {"error": "approval_not_found"}

    if (
        expected_action
        and normalize_action(approval.action) != normalize_action(expected_action)
    ):
        return {
            "error": "approval_action_mismatch",
            "message": "This approval was created for a different action."
        }

    if approval.approved:
        return {"error": "already_approved"}

    current_fingerprint = create_approval_fingerprint(current_details)

    if current_fingerprint != approval.fingerprint:
        return {
            "error": "approval_details_changed",
            "message": (
                "The action details have changed. "
                "A new approval is required."
            )
        }

    approval.approved = True
    approval.approved_at = datetime.now(timezone.utc)

    db.commit()

    return {
        "approval_id": approval.id,
        "status": "approved",
        "message": "User approval recorded."
    }


def verify_approval(
    db: Session,
    approval_id: str,
    user_id: str,
    current_details: dict,
    expected_action: Optional[str] = None
):
    """Verify approval immediately before executing an action."""
    approval = db.query(Approval).filter_by(
        id=approval_id,
        user_id=user_id
    ).first()

    if not approval:
        return {
            "approved": False,
            "reason": "approval_not_found"
        }

    if (
        expected_action
        and normalize_action(approval.action) != normalize_action(expected_action)
    ):
        return {
            "approved": False,
            "reason": "approval_action_mismatch"
        }

    if not approval.approved:
        return {
            "approved": False,
            "reason": "user_approval_required"
        }

    current_fingerprint = create_approval_fingerprint(current_details)

    if current_fingerprint != approval.fingerprint:
        return {
            "approved": False,
            "reason": "approval_details_changed"
        }

    return {
        "approved": True,
        "approval_id": approval.id,
        "action": approval.action
    }
