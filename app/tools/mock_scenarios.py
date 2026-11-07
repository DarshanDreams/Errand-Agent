import random
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScenarioConfig:
    seed: int = 42

    # Booking behaviour
    booking_outcome: Optional[str] = None
    timeout_booking_succeeds: Optional[bool] = None

    # Other failures
    force_price_change: bool = False
    force_slot_conflict: bool = False
    force_hold_expired: bool = False
    force_provider_cancellation: bool = False

    # Configurable values
    quoted_price_override: Optional[float] = None
    conflicting_slots: list = field(default_factory=list)

    # Cancellation behaviour
    cancellation_failures_remaining: int = 1


class ScenarioManager:

    def __init__(self, config: Optional[ScenarioConfig] = None):
        self.config = config or ScenarioConfig()
        self.rng = random.Random(self.config.seed)

    def get_booking_outcome(self):
        """
        Default distribution:
        70% success
        15% slot_taken
        15% timeout
        """
        if self.config.booking_outcome:
            return self.config.booking_outcome

        value = self.rng.random()

        if value < 0.70:
            return "success"
        elif value < 0.85:
            return "slot_taken"
        else:
            return "timeout"

    def timeout_succeeds(self):
        """
        Half of timeout scenarios actually book.
        """
        if self.config.timeout_booking_succeeds is not None:
            return self.config.timeout_booking_succeeds

        return self.rng.random() < 0.5

    def should_conflict(self, slot):
        return (
            self.config.force_slot_conflict
            or slot in self.config.conflicting_slots
        )


# Default scenario manager
scenario_manager = ScenarioManager()