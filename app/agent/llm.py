import httpx

from app.config import settings


class OpenRouterError(Exception):
    """Raised when an OpenRouter request fails."""


class OpenRouterClient:
    def __init__(self):
        self.api_key = settings.OPENROUTER_API_KEY
        self.model = settings.OPENROUTER_MODEL
        self.base_url = settings.OPENROUTER_BASE_URL.rstrip("/")

        # Allow slower model responses while keeping connection setup bounded.
        self.timeout = httpx.Timeout(
            connect=10.0,
            read=45.0,
            write=15.0,
            pool=10.0,
        )

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> dict:
        """
        Send a chat completion request to OpenRouter.

        Returns the assistant message and token usage.
        """

        if not self.api_key or self.api_key == "your_api_key_here":
            raise OpenRouterError(
                "OPENROUTER_API_KEY is missing. Check your .env file."
            )

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": settings.APP_NAME,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )

            if response.status_code != 200:
                raise OpenRouterError(
                    f"OpenRouter API error ({response.status_code}): "
                    f"{response.text[:500]}"
                )

            data = response.json()

            choices = data.get("choices", [])
            if not choices:
                raise OpenRouterError(
                    "OpenRouter returned no completion choices."
                )

            return {
                "message": choices[0]["message"],
                "usage": data.get("usage", {}),
                "model": data.get("model", self.model),
            }

        except httpx.TimeoutException as exc:
            raise OpenRouterError(
                "OpenRouter request timed out after 45 seconds."
            ) from exc

        except httpx.RequestError as exc:
            raise OpenRouterError(
                f"Could not connect to OpenRouter: {exc}"
            ) from exc

        except (KeyError, ValueError) as exc:
            raise OpenRouterError(
                "OpenRouter returned an invalid response."
            ) from exc


llm_client = OpenRouterClient()
