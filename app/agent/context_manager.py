
import json
from typing import Any

import tiktoken

from app.config import settings


class ContextManager:
    def __init__(self, token_budget: int | None = None):
        self.token_budget = token_budget or settings.CONTEXT_TOKEN_BUDGET

        try:
            self.encoder = tiktoken.encoding_for_model(
                settings.OPENROUTER_MODEL.split("/")[-1]
            )
        except KeyError:
            self.encoder = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate tokens used by a list of messages."""
        text = json.dumps(messages, ensure_ascii=False, default=str)
        return len(self.encoder.encode(text))

    def build_context(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, Any]],
        relevant_memories: list[dict[str, Any]] | None = None,
        excluded_text: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """
        Build a bounded context.

        The system prompt and relevant memories are retained.
        Recent conversation messages are added while they fit.
        """

        memory_text = ""

        if relevant_memories:
            memory_text = (
                "\n\nRelevant confirmed user preferences:\n"
                + json.dumps(
                    relevant_memories,
                    ensure_ascii=False,
                    default=str,
                )
            )

        system_message = {
            "role": "system",
            "content": system_prompt + memory_text,
        }

        context = [system_message]
        system_tokens = self.count_tokens(context)
        if system_tokens > self.token_budget:
            raise ValueError(
                "The system prompt and selected memories exceed the context token budget."
            )
        available_budget = self.token_budget - system_tokens

        selected_messages = []
        excluded = [value.casefold() for value in (excluded_text or [])]

        # Add recent messages first, without changing their original order.
        for message in reversed(conversation_history):
            if message.get("role") not in ("user", "assistant", "tool"):
                continue

            content = str(message.get("content", ""))
            if any(value in content.casefold() for value in excluded):
                continue

            candidate = {
                "role": message["role"],
                "content": content,
            }

            if message.get("tool_call_id"):
                candidate["tool_call_id"] = message["tool_call_id"]

            proposed = [candidate] + selected_messages

            if self.count_tokens(proposed) > available_budget:
                continue

            selected_messages = proposed

        context.extend(selected_messages)
        return context

    def get_context_stats(self, messages: list[dict[str, Any]]) -> dict:
        """Return context size information for tracing."""
        token_count = self.count_tokens(messages)

        return {
            "token_count": token_count,
            "token_budget": self.token_budget,
            "within_budget": token_count <= self.token_budget,
            "message_count": len(messages),
        }


context_manager = ContextManager()
