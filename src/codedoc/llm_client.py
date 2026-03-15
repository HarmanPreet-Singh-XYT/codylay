"""LLM client abstraction — supports Anthropic and OpenAI."""

import json
import os
import sys
import time
from typing import Dict, Any

import tiktoken

# Rate-limit error classes — loaded lazily so missing SDK doesn't crash import
_anthropic_rate_limit_error = None
_openai_rate_limit_error = None


def _get_rate_limit_errors():
    """Import provider-specific rate-limit error classes once."""
    global _anthropic_rate_limit_error, _openai_rate_limit_error
    if _anthropic_rate_limit_error is None:
        try:
            import anthropic
            _anthropic_rate_limit_error = anthropic.RateLimitError
        except ImportError:
            _anthropic_rate_limit_error = type(None)        # never matches
    if _openai_rate_limit_error is None:
        try:
            import openai
            _openai_rate_limit_error = openai.RateLimitError
        except ImportError:
            _openai_rate_limit_error = type(None)
    return _anthropic_rate_limit_error, _openai_rate_limit_error


def _extract_retry_after(exc) -> float:
    """Try to pull a retry-after value (seconds) from the API error response."""
    # Anthropic embeds it in response headers
    try:
        response = getattr(exc, "response", None)
        if response is not None:
            header = response.headers.get("retry-after")
            if header is not None:
                return max(float(header), 1.0)
    except Exception:
        pass
    return 0.0


class LLMClient:
    """Unified LLM client supporting Anthropic and OpenAI."""

    RATE_LIMIT_MAX_RETRIES = 5          # retry up to 5× on 429
    RATE_LIMIT_DEFAULT_WAIT = 60.0      # when no retry-after header

    def __init__(self, config):
        self.config = config
        self.provider = config.llm_provider
        self.model = config.llm_model
        self.max_tokens = config.max_tokens_per_call
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0

        if self.provider == "anthropic":
            try:
                import anthropic
                api_key = os.environ.get("ANTHROPIC_API_KEY")
                if not api_key:
                    raise ValueError(
                        "ANTHROPIC_API_KEY not set. "
                        "export ANTHROPIC_API_KEY=your-key"
                    )
                self.client = anthropic.Anthropic(api_key=api_key)
            except ImportError:
                raise ImportError("pip install anthropic")

        elif self.provider == "openai":
            try:
                import openai
                api_key = os.environ.get("OPENAI_API_KEY")
                if not api_key:
                    raise ValueError(
                        "OPENAI_API_KEY not set. "
                        "export OPENAI_API_KEY=your-key"
                    )
                self.client = openai.OpenAI(api_key=api_key)
            except ImportError:
                raise ImportError("pip install openai")

        try:
            self.tokenizer = tiktoken.encoding_for_model("gpt-4")
        except Exception:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text))

    # ── public entry point ──────────────────────────────────────────
    def call(
        self, system_prompt: str, user_prompt: str, retries: int = 3
    ) -> Dict[str, Any]:
        raw = ""
        for attempt in range(retries):
            try:
                raw = self._raw_call_with_rate_limit(system_prompt, user_prompt)
                self.call_count += 1
                return self._parse_json(raw)
            except json.JSONDecodeError:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return self._salvage_json(raw)
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

    # ── rate-limit-aware wrapper around _raw_call ───────────────────
    def _raw_call_with_rate_limit(
        self, system_prompt: str, user_prompt: str
    ) -> str:
        """Call _raw_call with automatic retry on 429 rate-limit errors."""
        anthropic_err, openai_err = _get_rate_limit_errors()
        rate_limit_errors = (anthropic_err, openai_err)

        for rate_attempt in range(self.RATE_LIMIT_MAX_RETRIES):
            try:
                return self._raw_call(system_prompt, user_prompt)
            except rate_limit_errors as exc:
                if rate_attempt >= self.RATE_LIMIT_MAX_RETRIES - 1:
                    raise                       # exhausted retries

                wait = _extract_retry_after(exc)
                if wait <= 0:
                    wait = self.RATE_LIMIT_DEFAULT_WAIT

                # Add a small jitter to avoid thundering herd
                wait += rate_attempt * 5

                print(
                    f"\n⏳  Rate limit hit — waiting {wait:.0f}s before "
                    f"retry {rate_attempt + 2}/{self.RATE_LIMIT_MAX_RETRIES}  "
                    f"(Ctrl+C to abort)",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait)

        # Shouldn't reach here, but just in case
        return self._raw_call(system_prompt, user_prompt)

    def _raw_call(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens
            return response.content[0].text

        elif self.provider == "openai":
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
            usage = response.usage
            if usage:
                self.total_input_tokens += usage.prompt_tokens
                self.total_output_tokens += usage.completion_tokens
            return response.choices[0].message.content

    def _parse_json(self, text: str) -> Dict[str, Any]:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return json.loads(text.strip())

    def _salvage_json(self, text: str) -> Dict[str, Any]:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {"error": "Failed to parse LLM response", "raw_response": text[:500]}

    def get_usage_stats(self) -> Dict[str, int]:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_calls": self.call_count,
        }