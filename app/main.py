
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config import settings
from app.database import models
from app.database.connection import init_db, get_db
from app.agent.orchestrator import orchestrator
from app.services.memory_service import MemoryService
from app.services.provider_event_service import handle_provider_cancellation


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered home service errand assistant",
    version="1.0.0",
    lifespan=lifespan,
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    conversation_id: Optional[str] = None
    user_id: str = "demo-user"


class ChatResponse(BaseModel):
    conversation_id: str
    state: str
    message: str
    booking_id: Optional[str] = None
    execution_seconds: float


class ProviderCancellationRequest(BaseModel):
    booking_id: str = Field(min_length=1)


class MemoryCorrectionRequest(BaseModel):
    value: str | float | int | dict | list


@app.get("/")
def root():
    return {
        "message": "Errand Agent is running",
        "environment": settings.ENVIRONMENT,
    }


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))

    return {
        "status": "healthy",
        "database": "connected",
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
):
    try:
        result = await orchestrator.handle_message(
            db=db,
            user_message=request.message,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
        )

        return result

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while processing the request.",
        ) from exc


@app.get("/users/{user_id}/memories")
def inspect_memories(
    user_id: str,
    include_inferred: bool = False,
    db: Session = Depends(get_db),
):
    return {
        "memories": MemoryService(db).get_memories(
            user_id,
            include_inferred=include_inferred,
        )
    }


@app.put("/users/{user_id}/memories/{key}")
def correct_memory(
    user_id: str,
    key: str,
    request: MemoryCorrectionRequest,
    db: Session = Depends(get_db),
):
    try:
        memory = MemoryService(db).correct_memory(
            user_id=user_id,
            key=key,
            new_value=request.value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"key": memory.key, "value": memory.value, "source": memory.source}


@app.delete("/users/{user_id}/memories/{key}", status_code=204)
def forget_memory(
    user_id: str,
    key: str,
    db: Session = Depends(get_db),
):
    if not MemoryService(db).forget_memory(user_id, key):
        raise HTTPException(status_code=404, detail="Saved preference not found.")
    return Response(status_code=204)


@app.post("/events/provider-cancelled")
def provider_cancelled(
    request: ProviderCancellationRequest,
    db: Session = Depends(get_db),
):
    result = handle_provider_cancellation(db, request.booking_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result
