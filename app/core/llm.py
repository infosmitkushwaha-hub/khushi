"""Claude access for every agent, with two interchangeable backends.

    LLM_AUTH=oauth    -> Claude Code CLI (`claude -p`) using CLAUDE_CODE_OAUTH_TOKEN (Claude subscription), like JAY
    LLM_AUTH=api_key  -> Anthropic Python SDK using ANTHROPIC_API_KEY (pay per use)

Agents only ever call `llm.text(...)` or `llm.json(...)`, so switching is a one-line .env change.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class Backend(Protocol):
    name: str

    def complete(self, system: str, prompt: str, model: str, web_search: bool = False) -> str: ...


class OAuthBackend:
    """Headless Claude Code. No session, no MCP, and no tools except WebSearch when explicitly asked for."""

    name = "oauth"

    def __init__(self, s: Settings):
        if not s.claude_code_oauth_token:
            raise LLMError("LLM_AUTH=oauth but CLAUDE_CODE_OAUTH_TOKEN is empty. Run `claude setup-token`.")
        self.bin = shutil.which(s.claude_bin) or s.claude_bin
        self.token = s.claude_code_oauth_token
        self.timeout = s.llm_timeout

    def complete(self, system: str, prompt: str, model: str, web_search: bool = False) -> str:
        env = dict(os.environ)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = self.token
        env.pop("ANTHROPIC_API_KEY", None)  # otherwise the CLI would bill the API key instead
        cmd = [
            self.bin, "-p",
            "--output-format", "json",
            "--model", model,
            *(["--tools", "WebSearch", "--allowedTools", "WebSearch"] if web_search else ["--tools", ""]),
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt", system,
        ]
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                timeout=self.timeout, env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude -p timed out after {self.timeout}s") from e
        except FileNotFoundError as e:
            raise LLMError(f"Claude Code CLI not found at '{self.bin}'. Install it or set CLAUDE_BIN.") from e
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude -p returned non-JSON (exit {proc.returncode}): {proc.stderr[-500:]}") from e
        if data.get("is_error") or proc.returncode != 0:
            raise LLMError(f"claude -p failed: {str(data.get('result'))[:500]}")
        return data.get("result", "")


class ApiKeyBackend:
    name = "api_key"

    def __init__(self, s: Settings):
        if not s.anthropic_api_key:
            raise LLMError("LLM_AUTH=api_key but ANTHROPIC_API_KEY is empty.")
        import anthropic

        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.llm_timeout)

    def complete(self, system: str, prompt: str, model: str, web_search: bool = False) -> str:
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}] if web_search else []
        msg = self.client.messages.create(
            model=model, max_tokens=8000, system=system, messages=[{"role": "user", "content": prompt}],
            **({"tools": tools} if tools else {}),
        )
        return "".join(b.text for b in msg.content if b.type == "text")


def extract_json(text: str) -> str:
    """Pull the JSON object out of a reply that may be wrapped in ```json fences or prose."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        raise LLMError("no JSON found in model reply")
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start : end + 1]


class LLM:
    def __init__(self, backend: Backend, s: Settings):
        self.backend = backend
        self.s = s

    @property
    def mode(self) -> str:
        return self.backend.name

    def _model(self, smart: bool) -> str:
        return self.s.llm_model_smart if smart else self.s.llm_model_fast

    def text(self, prompt: str, system: str = "", smart: bool = True) -> str:
        return self.backend.complete(system or "You are a helpful assistant.", prompt, self._model(smart)).strip()

    def json(self, prompt: str, schema: type[T], system: str = "", smart: bool = True,
             web_search: bool = False) -> T:
        """Ask for JSON matching `schema`; validate; retry once with the validation error."""
        instruction = (
            "\n\nReturn ONLY one JSON object (no prose, no markdown) that validates against this JSON Schema:\n"
            + json.dumps(schema.model_json_schema())
        )
        reply = self.backend.complete(system or "You return strict JSON.", prompt + instruction, self._model(smart),
                                      web_search)
        try:
            return schema.model_validate_json(extract_json(reply))
        except (ValidationError, LLMError) as first_error:
            fix = (
                f"{prompt}{instruction}\n\nYour previous reply was invalid:\n{reply[:3000]}\n\n"
                f"Error: {first_error}\nReturn the corrected JSON only."
            )
            reply = self.backend.complete(system or "You return strict JSON.", fix, self._model(smart))
            try:
                return schema.model_validate_json(extract_json(reply))
            except ValidationError as e:
                raise LLMError(f"model output failed schema {schema.__name__}: {e}") from e


def get_llm(s: Settings | None = None) -> LLM:
    s = s or get_settings()
    backend: Backend = OAuthBackend(s) if s.llm_auth == "oauth" else ApiKeyBackend(s)
    return LLM(backend, s)
