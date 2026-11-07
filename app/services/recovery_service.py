
from typing import Any

from app.services.booking_service import booking_service


class RecoveryService:
    """
    Safely handles uncertain booking outcomes.

    Always checks the authoritative booking status before
    considering another booking attempt.
    """

    def check_booking(
        self,
        hold_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:

        status = booking_service.verify_booking(
            hold_id=hold_id,
            idempotency_key=idempotency_key,
        )

        booking_status = status.get("status")

        if booking_status == "confirmed":
            return {
                "success": True,
                "action": "none",
                "message": "Booking is confirmed.",
                "booking": status,
            }

        if booking_status == "unknown":
            return {
                "success": False,
                "action": "wait_and_verify",
                "message": (
                    "Booking outcome is uncertain. "
                    "Do not retry until the status is verified."
                ),
                "booking": status,
            }

        if booking_status == "not_booked":
            return {
                "success": False,
                "action": "retry_allowed",
                "message": (
                    "No booking exists for this hold. "
                    "A retry may be considered using the same "
                    "idempotency key."
                ),
                "booking": status,
            }

        if booking_status == "not_found":
            return {
                "success": False,
                "action": "investigate",
                "message": (
                    "No matching booking or hold was found. "
                    "Do not create another booking automatically."
                ),
                "booking": status,
            }

        # Some failed idempotency records return their original
        # error result rather than a status field.
        return {
            "success": False,
            "action": "investigate",
            "message": (
                "The booking status could not be confirmed. "
                "Do not retry automatically."
            ),
            "booking": status,
        }

    def retry_booking(
        self,
        hold_id: str,
        idempotency_key: str,
        approval_id: str,
    ) -> dict[str, Any]:
        """
        Retry only after status confirms that the hold was not booked.

        The original idempotency key is reused to prevent duplicates.
        """

        recovery = self.check_booking(
            hold_id=hold_id,
            idempotency_key=idempotency_key,
        )

        if recovery["action"] != "retry_allowed":
            return {
                "success": False,
                "error": "retry_not_safe",
                "recovery": recovery,
            }

        result = booking_service.confirm_booking(
            hold_id=hold_id,
            idempotency_key=idempotency_key,
            approval_id=approval_id,
        )

        # Even after a retry, verify the authoritative status.
        verified = self.check_booking(
            hold_id=hold_id,
            idempotency_key=idempotency_key,
        )

        return {
            "operation_result": result,
            "verification": verified,
        }


recovery_service = RecoveryService()
