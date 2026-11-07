from app.tools.booking_tools import (
    hold_slot,
    book,
    get_booking_status,
    cancel,
    reschedule,
    list_bookings,
)


class BookingService:

    def create_hold(self, provider_id, slot):
        return hold_slot(
            provider_id=provider_id,
            slot=slot
        )

    def confirm_booking(
        self,
        hold_id,
        idempotency_key,
        approval_id,
        user_id="demo-user",
    ):
        if not approval_id:
            return {
                "success": False,
                "error": "approval_required",
                "message": "Explicit approval is required before booking."
            }

        return book(
            hold_id=hold_id,
            idempotency_key=idempotency_key,
            approval_id=approval_id,
            user_id=user_id,
        )

    def verify_booking(self, hold_id=None, idempotency_key=None):
        return get_booking_status(
            hold_id=hold_id,
            idempotency_key=idempotency_key
        )

    def cancel_booking(self, booking_id, idempotency_key, approval_id=None):
        return cancel(
            booking_id=booking_id,
            idempotency_key=idempotency_key,
            approval_id=approval_id
        )

    def reschedule_booking(
        self,
        booking_id,
        slot,
        idempotency_key,
        approval_id=None
    ):
        return reschedule(
            booking_id=booking_id,
            slot=slot,
            idempotency_key=idempotency_key,
            approval_id=approval_id
        )

    def get_user_bookings(self, user_id):
        return list_bookings(user_id=user_id)


booking_service = BookingService()