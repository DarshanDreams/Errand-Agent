import hashlib
import json
import uuid

from datetime import datetime, timedelta, timezone

from app.database.connection import SessionLocal
from app.database.models import (
    Booking,
    SlotHold,
    IdempotencyRecord
)

from app.tools.provider_tools import PROVIDERS
from app.tools.mock_scenarios import scenario_manager
from app.services.approval_service import verify_approval


def now():
    return datetime.now(timezone.utc)


def normalize_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_provider_data(provider_id):
    for provider in PROVIDERS:
        if provider["id"] == provider_id:
            return provider
    return None


def get_quoted_price(provider_id):
    provider = get_provider_data(provider_id)

    if not provider:
        raise ValueError("provider_not_found")

    override = scenario_manager.config.quoted_price_override

    if override is not None:
        return float(override)

    return float(provider["price"])


def hold_slot(provider_id: str, slot: str):
    provider = get_provider_data(provider_id)

    if not provider:
        return {"error": "provider_not_found"}

    if slot not in provider["available_slots"]:
        return {"error": "slot_unavailable"}

    if scenario_manager.config.force_hold_expired:
        return {"error": "hold_expired"}

    db = SessionLocal()

    try:
        existing_holds = db.query(SlotHold).filter(
            SlotHold.provider_id == provider_id,
            SlotHold.slot == slot,
            SlotHold.status == "held"
        ).all()

        for existing in existing_holds:
            if normalize_datetime(existing.expires_at) > now():
                return {"error": "slot_already_held"}

            existing.status = "expired"

        hold = SlotHold(
            id=str(uuid.uuid4()),
            provider_id=provider_id,
            slot=slot,
            quoted_price=get_quoted_price(provider_id),
            status="held",
            expires_at=now() + timedelta(minutes=10)
        )

        db.add(hold)
        db.commit()
        db.refresh(hold)

        return {
            "hold_id": hold.id,
            "provider_id": provider_id,
            "slot": slot,
            "quoted_price": hold.quoted_price,
            "expires_at": hold.expires_at.isoformat(),
            "status": "held"
        }

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def create_booking(db, hold, idempotency_key, user_id="demo-user"):
    existing = db.query(Booking).filter_by(
        idempotency_key=idempotency_key
    ).first()

    if existing:
        return existing

    provider = get_provider_data(hold.provider_id)

    if not provider:
        raise ValueError("provider_not_found")

    booking = Booking(
        id=str(uuid.uuid4()),
        user_id=user_id,
        provider_id=hold.provider_id,
        service=provider["service"],
        slot={"datetime": hold.slot},
        price=hold.quoted_price,
        status="confirmed",
        hold_id=hold.id,
        idempotency_key=idempotency_key
    )

    hold.status = "booked"
    db.add(booking)
    db.flush()

    return booking


def book(
    hold_id: str,
    idempotency_key: str,
    approval_id: str = None,
    user_id: str = "demo-user",
):
    db = SessionLocal()

    try:
        request_hash = hashlib.sha256(
            json.dumps(
                {"hold_id": hold_id},
                sort_keys=True
            ).encode()
        ).hexdigest()

        record = db.query(IdempotencyRecord).filter_by(
            idempotency_key=idempotency_key
        ).first()

        # A completed or failed operation is returned without repeating its side effect.
        if record:
            if record.request_hash != request_hash:
                return {
                    "error": "idempotency_key_reused_with_different_request"
                }

            if record.status == "completed":
                booking = db.query(Booking).filter_by(
                    idempotency_key=idempotency_key
                ).first()
                if booking and booking.user_id != user_id:
                    return {"error": "booking_owner_mismatch"}
                return record.result

            if record.status == "failed":
                return record.result

            if record.status != "retryable":
                return {
                    "error": "operation_in_progress",
                    "requires_status_check": True
                }

        hold = db.query(SlotHold).filter_by(
            id=hold_id
        ).first()

        if not hold:
            return {"error": "hold_not_found"}

        if hold.status != "held":
            return {"error": "hold_not_active"}

        if normalize_datetime(hold.expires_at) <= now():
            hold.status = "expired"
            db.commit()
            return {"error": "hold_expired"}

        if not approval_id:
            return {
                "error": "approval_required",
                "message": "Explicit user approval is required before booking."
            }

        approved_details = {
            "provider_id": hold.provider_id,
            "slot": hold.slot,
            "price": hold.quoted_price
        }

        approval_check = verify_approval(
            db=db,
            approval_id=approval_id,
            user_id=user_id,
            current_details=approved_details,
            expected_action="book"
        )

        if not approval_check["approved"]:
            return {
                "error": "approval_invalid",
                "reason": approval_check["reason"],
                "message": "Booking was blocked because valid booking approval was not found."
            }

        if record is None:
            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                operation="book",
                request_hash=request_hash,
                status="started"
            )
            db.add(record)
        else:
            record.status = "started"
            record.result = None
        db.commit()

        outcome = scenario_manager.get_booking_outcome()

        if outcome == "slot_taken":
            result = {"error": "slot_taken"}

            record.status = "failed"
            record.result = result
            hold.status = "unavailable"

            db.commit()
            return result

        if outcome == "timeout":
            if scenario_manager.timeout_succeeds():
                booking = create_booking(
                    db, hold, idempotency_key, user_id
                )

                record.status = "completed"
                record.result = {
                    "booking_id": booking.id,
                    "status": "confirmed",
                    "message": "Booking created"
                }

                db.commit()

            else:
                record.status = "retryable"
                record.result = {
                    "status": "not_booked",
                    "message": "Booking did not succeed"
                }

                db.commit()

            return {
                "error": "timeout",
                "message": "Booking response timed out. Verify status."
            }

        if outcome == "success":
            booking = create_booking(
                db, hold, idempotency_key, user_id
            )

            record.status = "completed"
            record.result = {
                "booking_id": booking.id,
                "status": "confirmed",
                "message": "Booking created"
            }

            db.commit()
            return record.result

        return {"error": "unknown_booking_outcome"}

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def get_booking_status(hold_id=None, idempotency_key=None):
    db = SessionLocal()

    try:
        booking = None

        if idempotency_key:
            booking = db.query(Booking).filter_by(
                idempotency_key=idempotency_key
            ).first()

        if not booking and hold_id:
            booking = db.query(Booking).filter_by(
                hold_id=hold_id
            ).first()

        if booking:
            return {
                "status": booking.status,
                "booking_id": booking.id,
                "provider_id": booking.provider_id,
                "slot": booking.slot
            }

        if idempotency_key:
            record = db.query(IdempotencyRecord).filter_by(
                idempotency_key=idempotency_key
            ).first()

            if record and record.status == "failed":
                return record.result

            if record and record.status == "retryable":
                return record.result

            if record and record.status == "started":
                return {"status": "unknown"}

        if hold_id:
            hold = db.query(SlotHold).filter_by(
                id=hold_id
            ).first()

            if hold:
                return {
                    "status": "not_booked",
                    "hold_status": hold.status
                }

        return {"status": "not_found"}

    finally:
        db.close()


def cancel(
    booking_id: str,
    idempotency_key: str,
    approval_id: str = None
):
    db = SessionLocal()

    try:
        booking = db.query(Booking).filter_by(
            id=booking_id
        ).first()

        if not booking:
            return {"error": "booking_not_found"}

        request_hash = hashlib.sha256(
            f"{booking_id}:cancel".encode()
        ).hexdigest()

        record = db.query(IdempotencyRecord).filter_by(
            idempotency_key=idempotency_key
        ).first()

        if record:
            if record.request_hash != request_hash:
                return {
                    "error": "idempotency_key_reused_with_different_request"
                }

            if record.status == "completed":
                return record.result

        if booking.status == "cancelled":
            return {
                "status": "cancelled",
                "booking_id": booking.id
            }

        if booking.status != "confirmed":
            return {"error": "booking_not_active"}

        if not approval_id:
            return {
                "error": "approval_required",
                "message": "Explicit user approval is required before cancellation."
            }

        approval_details = {
            "booking_id": booking.id,
            "provider_id": booking.provider_id,
            "service": booking.service,
            "slot": booking.slot
        }

        approval_check = verify_approval(
            db=db,
            approval_id=approval_id,
            user_id=booking.user_id,
            current_details=approval_details,
            expected_action="cancel"
        )

        if not approval_check["approved"]:
            return {
                "error": "approval_invalid",
                "reason": approval_check["reason"],
                "message": "Cancellation was blocked because valid cancellation approval was not found."
            }

        if not record:
            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                operation="cancel",
                request_hash=request_hash,
                status="started"
            )
            db.add(record)
            db.commit()

        if scenario_manager.config.cancellation_failures_remaining > 0:
            scenario_manager.config.cancellation_failures_remaining -= 1

            return {
                "error": "service_unavailable",
                "status_code": 503,
                "retryable": True
            }

        booking.status = "cancelled"

        result = {
            "status": "cancelled",
            "booking_id": booking.id
        }

        record.status = "completed"
        record.result = result

        db.commit()
        return result

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def reschedule(
    booking_id: str,
    slot: str,
    idempotency_key: str,
    approval_id: str = None
):
    db = SessionLocal()

    try:
        booking = db.query(Booking).filter_by(
            id=booking_id
        ).first()

        if not booking:
            return {"error": "booking_not_found"}

        if booking.status != "confirmed":
            return {"error": "booking_not_active"}

        old_slot = booking.slot

        request_hash = hashlib.sha256(
            f"{booking_id}:{slot}".encode()
        ).hexdigest()

        record = db.query(IdempotencyRecord).filter_by(
            idempotency_key=idempotency_key
        ).first()

        if record:
            if record.request_hash != request_hash:
                return {
                    "error": "idempotency_key_reused_with_different_request"
                }

            if record.status == "completed":
                return record.result

        if not approval_id:
            return {
                "error": "approval_required",
                "message": "Explicit user approval is required before rescheduling."
            }

        approval_details = {
            "booking_id": booking.id,
            "provider_id": booking.provider_id,
            "old_slot": old_slot,
            "new_slot": slot
        }

        approval_check = verify_approval(
            db=db,
            approval_id=approval_id,
            user_id=booking.user_id,
            current_details=approval_details,
            expected_action="reschedule"
        )

        if not approval_check["approved"]:
            return {
                "error": "approval_invalid",
                "reason": approval_check["reason"],
                "message": "Rescheduling was blocked because valid rescheduling approval was not found."
            }

        provider = get_provider_data(booking.provider_id)

        if not provider:
            return {"error": "provider_not_found"}

        if scenario_manager.should_conflict(slot):
            return {"error": "slot_conflict"}

        if slot not in provider["available_slots"]:
            return {"error": "slot_unavailable"}

        if not record:
            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                operation="reschedule",
                request_hash=request_hash,
                status="started"
            )
            db.add(record)

        booking.slot = {"datetime": slot}

        result = {
            "status": "rescheduled",
            "booking_id": booking.id,
            "new_slot": slot
        }

        record.status = "completed"
        record.result = result

        db.commit()
        return result

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def list_bookings(user_id: str):
    db = SessionLocal()

    try:
        bookings = db.query(Booking).filter_by(
            user_id=user_id
        ).all()

        return [
            {
                "booking_id": item.id,
                "provider_id": item.provider_id,
                "service": item.service,
                "slot": item.slot,
                "status": item.status
            }
            for item in bookings
        ]

    finally:
        db.close()
