
import time
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import SessionLocal
from app.database.models import Trace


def _safe_data(data: Any) -> Any:
    """Convert trace data into JSON-safe values and limit its size."""
    if data is None:
        return None

    if isinstance(data, dict):
        return {
            str(key): _safe_data(value)
            for key, value in data.items()
            if str(key).lower() not in {
                "api_key",
                "authorization",
                "password",
                "secret",
                "token",
            }
        }

    if isinstance(data, (list, tuple)):
        return [_safe_data(item) for item in data[:50]]

    if isinstance(data, (str, int, float, bool)):
        if isinstance(data, str):
            return data[:2000]
        return data

    return str(data)[:500]


class Tracer:
    def __init__(self, db: Session | None = None):
        self.db = db

    def log_event(
        self,
        user_id: str,
        event_type: str,
        event_name: str,
        conversation_id: str | None = None,
        arguments: dict | None = None,
        result: Any = None,
        latency_ms: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost: float | None = None,
    ) -> None:
        """Persist one sanitized trace event."""

        if not settings.ENABLE_TRACING:
            return

        trace = Trace(
            user_id=user_id,
            conversation_id=conversation_id,
            event_type=event_type,
            event_name=event_name,
            arguments=_safe_data(arguments),
            result=_safe_data(result),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
        )

        if self.db is not None:
            self.db.add(trace)
            self.db.commit()
            return

        db = SessionLocal()

        try:
            db.add(trace)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def log_state(
        self,
        user_id: str,
        conversation_id: str,
        previous_state: str,
        new_state: str,
    ) -> None:
        self.log_event(
            user_id=user_id,
            conversation_id=conversation_id,
            event_type="state_transition",
            event_name=f"{previous_state} -> {new_state}",
            arguments={"previous_state": previous_state},
            result={"new_state": new_state},
        )


class Timer:
    """Small helper for measuring operation latency."""

    def __init__(self):
        self.started_at = 0.0

    def start(self) -> None:
        self.started_at = time.perf_counter()

    def elapsed_ms(self) -> float:
        if not self.started_at:
            return 0.0

        return round(
            (time.perf_counter() - self.started_at) * 1000,
            2,
        )


tracer = Tracer()
