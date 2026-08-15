"""Thin wrapper around the Anthropic SDK.

Three things this centralises:

  * **Structured output.** Every call returns a validated Pydantic model via
    ``messages.parse()``, so no downstream code parses JSON out of prose.
  * **Prompt caching.** The master profile is identical across every job in a
    run, so it goes in a cached system block. After the first call each job
    re-reads it at ~10% of input cost instead of paying full price.
  * **Failure modes.** Refusals arrive as a successful HTTP 200 with
    ``stop_reason == "refusal"``, so ``content`` must never be read blind.
"""

from __future__ import annotations

import logging
from typing import Optional, TypeVar

import anthropic
from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: `output_config.effort` is rejected outright by models that predate it
#: (Haiku 4.5, Sonnet 4.5). Sending it there 400s every call, so the effort
#: hint is dropped for anything not on this list.
_EFFORT_CAPABLE = (
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-opus-4-5", "claude-sonnet-5", "claude-sonnet-4-6",
    "claude-fable-5", "claude-mythos-5",
)


def _supports_effort(model: str) -> bool:
    return any(model.startswith(prefix) for prefix in _EFFORT_CAPABLE)


class LLMError(RuntimeError):
    """Raised when a call fails in a way the caller should handle."""


class LLMRefusal(LLMError):
    """Claude's safety classifiers declined the request."""


class Claude:
    def __init__(self, api_key: Optional[str] = None, max_retries: int = 4) -> None:
        if not api_key:
            raise LLMError(
                "No Anthropic API key found. Put ANTHROPIC_API_KEY in .env "
                "(copy .env.example) or export it in your shell."
            )
        # Catch the placeholder before spending a round trip on a guaranteed 401.
        if api_key.rstrip(". ") in ("sk-ant", "sk-ant-") or api_key.endswith("..."):
            raise LLMError(
                "ANTHROPIC_API_KEY in .env is still the placeholder from "
                "`.env.example` (sk-ant-...). Replace it with your real key "
                "from https://console.anthropic.com/settings/keys — and note "
                "that re-running `cp .env.example .env` overwrites it."
            )
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries)
        self.key_source = ".env"  # overwritten by the CLI with the real origin
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0

    # ------------------------------------------------------------------ #

    def parse(
        self,
        *,
        model: str,
        schema: type[T],
        instructions: str,
        user_content: str,
        cached_context: Optional[str] = None,
        max_tokens: int = 8000,
        effort: Optional[str] = None,
    ) -> T:
        """Run one structured-output call and return a validated ``schema``.

        ``cached_context`` is content that repeats across many calls (the master
        profile). It is placed first in the system prompt behind a cache
        breakpoint; ``instructions`` follows it and varies per call type.
        """
        system: list[dict] = []
        if cached_context:
            system.append(
                {
                    "type": "text",
                    "text": cached_context,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        system.append({"type": "text", "text": instructions})

        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            "output_format": schema,
        }
        if effort and _supports_effort(model):
            kwargs["output_config"] = {"effort": effort}

        try:
            response = self.client.messages.parse(**kwargs)
        except anthropic.BadRequestError as exc:
            message = str(exc)
            # Structured outputs compile the schema into a grammar server-side,
            # and that compiler has both a complexity ceiling and a time budget.
            # A nested schema can trip either one ("Schema is too complex",
            # "Grammar compilation timed out"). Plain JSON skips compilation
            # entirely and Pydantic still validates the result, so the only
            # thing lost is the server-side guarantee.
            lowered = message.lower()
            if any(
                marker in lowered
                for marker in ("too complex", "grammar", "compilation")
            ):
                reason = (
                    "schema too complex" if "too complex" in lowered
                    else "grammar compilation failed"
                )
                log.warning(
                    "%s for %s on %s — retrying without structured outputs",
                    reason, schema.__name__, model,
                )
                return self._parse_via_json(
                    model=model, schema=schema, instructions=instructions,
                    user_content=user_content, cached_context=cached_context,
                    max_tokens=max_tokens,
                )
            # Billing arrives as a 400, not a 402, so it reads as a malformed
            # request unless it's called out by name.
            if "credit balance" in message.lower():
                raise LLMError(
                    "Your Anthropic API account has no credits.\n\n"
                    "API usage is billed separately from a Claude Pro/Max "
                    "subscription — a Pro plan does not include API credits.\n"
                    "Add credit at https://console.anthropic.com/settings/billing "
                    "(the minimum purchase covers many runs).\n\n"
                    "To cut per-run cost, lower `scoring.max_tailored_per_run` "
                    "and point `models.jd_analysis` at a smaller model in "
                    "config.yaml — that call runs once per posting and dominates "
                    "the bill."
                ) from exc
            # Effort inside output_config can collide with the format the parse
            # helper injects on some SDK versions. Losing effort is survivable;
            # losing the call is not.
            if effort and "output_config" in message:
                kwargs.pop("output_config", None)
                response = self.client.messages.parse(**kwargs)
            else:
                raise LLMError(f"Bad request to Claude: {exc}") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError(
                f"Anthropic rejected the API key (401).\n\n"
                f"The key in use came from: {self.key_source}\n\n"
                "If a different key works in your terminal, the two disagree — "
                "an `export ANTHROPIC_API_KEY=...` in your shell overrides .env "
                "and is invisible to the scheduled daily run. Put the working "
                "key in .env so both paths use it.\n"
                "Otherwise the key may be revoked, or scoped to a different "
                "org/workspace: https://console.anthropic.com/settings/keys"
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError(
                "The API key is valid but lacks access to this model (403). "
                "Check the workspace's model permissions, or set a different "
                "model under `models:` in config.yaml."
            ) from exc
        except anthropic.RateLimitError as exc:
            raise LLMError(f"Rate limited by the Anthropic API: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc}") from exc

        self._record_usage(response)

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise LLMRefusal(f"Claude declined this request (category: {category})")

        if response.stop_reason == "max_tokens":
            raise LLMError(
                f"Response hit the {max_tokens}-token cap before finishing. "
                "Raise max_tokens for this call."
            )

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise LLMError(
                f"Claude returned no parseable {schema.__name__}; "
                f"stop_reason={response.stop_reason}"
            )
        return parsed

    # ------------------------------------------------------------------ #

    def _parse_via_json(
        self,
        *,
        model: str,
        schema: type[T],
        instructions: str,
        user_content: str,
        cached_context: Optional[str],
        max_tokens: int,
    ) -> T:
        """Fallback for when grammar compilation fails.

        Asks for raw JSON against the same schema and validates client-side.
        Slightly less reliable than structured outputs — the model can wrap the
        object in prose or a code fence — so both are stripped before parsing.
        """
        import json

        json_schema = json.dumps(schema.model_json_schema(), indent=2)
        system: list[dict] = []
        if cached_context:
            system.append(
                {
                    "type": "text",
                    "text": cached_context,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        system.append(
            {
                "type": "text",
                "text": (
                    f"{instructions}\n\n"
                    "Reply with a single JSON object conforming to this schema "
                    "and nothing else — no prose, no code fence:\n\n"
                    f"{json_schema}"
                ),
            }
        )

        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        self._record_usage(response)

        if response.stop_reason == "refusal":
            raise LLMRefusal("Claude declined this request")

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

        # Trim a ```json fence and any leading/trailing prose.
        if text.startswith("```"):
            text = text.split("```")[1]
            text = text[4:] if text.lower().startswith("json") else text
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

        try:
            return schema.model_validate_json(text)
        except Exception as exc:
            raise LLMError(
                f"Could not parse a valid {schema.__name__} from the response. "
                f"Try a more capable model for this stage in config.yaml. ({exc})"
            ) from exc

    # ------------------------------------------------------------------ #

    def _record_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

    def usage_summary(self) -> str:
        return (
            f"{self.calls} Claude calls · "
            f"{self.input_tokens:,} in / {self.output_tokens:,} out tokens · "
            f"{self.cached_tokens:,} read from cache"
        )
