import uuid

from app.database.models import Booking
from app.services.approval_service import (
    request_approval,
    approve_action,
    verify_approval,
)
from app.tools.booking_tools import cancel, reschedule


def list_confirmed_bookings(db, user_id: str):
    bookings = (
        db.query(Booking)
        .filter_by(user_id=user_id, status="confirmed")
        .order_by(Booking.created_at.desc())
        .all()
    )

    return [
        {
            "booking_id": booking.id,
            "provider_id": booking.provider_id,
            "service": booking.service,
            "slot": booking.slot,
            "price": booking.price,
            "status": booking.status,
        }
        for booking in bookings
    ]


def get_confirmed_booking(db, user_id: str, booking_id: str):
    return (
        db.query(Booking)
        .filter_by(
            id=booking_id,
            user_id=user_id,
            status="confirmed",
        )
        .first()
    )


def prepare_action(
    db,
    user_id: str,
    conversation_id: str,
    booking_id: str,
    action: str,
    new_slot: str = None,
):
    if action not in {"cancel", "reschedule"}:
        return {"error": "unsupported_action"}

    booking = get_confirmed_booking(db, user_id, booking_id)

    if not booking:
        return {"error": "booking_not_found_or_inactive"}

    if action == "cancel":
        details = {
            "booking_id": booking.id,
            "provider_id": booking.provider_id,
            "service": booking.service,
            "slot": booking.slot,
        }

    else:
        if not new_slot:
            return {"error": "new_slot_required"}

        details = {
            "booking_id": booking.id,
            "provider_id": booking.provider_id,
            "old_slot": booking.slot,
            "new_slot": new_slot,
        }

    approval = request_approval(
        db=db,
        user_id=user_id,
        conversation_id=conversation_id,
        action=action,
        details=details,
    )

    return {
        "status": "awaiting_approval",
        "action": action,
        "approval_id": approval["approval_id"],
        "details": details,
        "idempotency_key": str(uuid.uuid4()),
    }


def execute_approved_action(
    db,
    user_id: str,
    pending_action: dict,
):
    action = pending_action.get("action")
    details = pending_action.get("details", {})
    approval_id = pending_action.get("approval_id")
    idempotency_key = pending_action.get("idempotency_key")

    if action not in {"cancel", "reschedule"}:
        return {"error": "unsupported_action"}

    if not approval_id or not idempotency_key:
        return {"error": "missing_approval_or_idempotency_key"}

    booking_id = details.get("booking_id")

    booking = db.query(Booking).filter_by(
        id=booking_id,
        user_id=user_id,
    ).first()

    if not booking:
        return {"error": "booking_not_found"}

    if booking.status != "confirmed":
        return {"error": "booking_not_active"}

    if action == "cancel":
        current_details = {
            "booking_id": booking.id,
            "provider_id": booking.provider_id,
            "service": booking.service,
            "slot": booking.slot,
        }
    else:
        current_details = {
            "booking_id": booking.id,
            "provider_id": booking.provider_id,
            "old_slot": booking.slot,
            "new_slot": details.get("new_slot"),
        }

    # First attempt: consume the user's explicit approval.
    approval = approve_action(
        db=db,
        approval_id=approval_id,
        user_id=user_id,
        current_details=current_details,
        expected_action=action,
    )

    if approval.get("status") != "approved":
        # A previous attempt may already have consumed the approval.
        # Verify the same approval before allowing a retry.
        verification = verify_approval(
            db=db,
            approval_id=approval_id,
            user_id=user_id,
            current_details=current_details,
            expected_action=action,
        )

        if not verification.get("approved"):
            return {
                "error": "approval_invalid",
                "reason": verification.get("reason"),
            }

    # Keep the same idempotency key across retries.
    if action == "cancel":
        return cancel(
            booking_id=booking.id,
            idempotency_key=idempotency_key,
            approval_id=approval_id,
        )

    return reschedule(
        booking_id=booking.id,
        slot=details["new_slot"],
        idempotency_key=idempotency_key,
        approval_id=approval_id,
    )