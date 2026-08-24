"""Deterministic model routing with no silent fallback.

ADR-008 sets the policy: Claude for compact, judgment-heavy review synthesis,
OpenAI for extensive context or batch evaluation. The routing decision is
recorded so a benchmark figure can be attributed to the model that produced it.

Nothing here falls back. A missing provider, model, or key raises, because a run
that quietly used a different model than the one it reports would corrupt every
measurement taken from it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Protocol

from .config import Settings
from .findings import DraftedFindings

ProviderName = Literal["anthropic", "openai", "mock"]

#: Above this estimate the prompt stops being "compact" and routing prefers the
#: extensive-context provider. Characters, not tokens: the router must decide
#: before a tokenizer is available, and the ratio is stable enough for a policy.
LARGE_PROMPT_CHARACTERS = 120_000


class ProviderError(RuntimeError):
    """Raised when a provider cannot be used exactly as configured."""


@dataclass(frozen=True)
class ModelChoice:
    """The routing decision for one call, recorded alongside the run."""

    provider: ProviderName
    model: str
    reason: str


class ModelProvider(Protocol):
    """Drafts findings from a system prompt and a user prompt."""

    def draft(self, *, system: str, user: str, choice: ModelChoice) -> DraftedFindings: ...


def route(
    *,
    settings: Settings,
    prompt_characters: int,
    task: str = "review_synthesis",
) -> ModelChoice:
    """Choose a provider and model before the call, deterministically."""

    policy = settings.model_policy.strip().lower()

    if policy == "mock":
        return ModelChoice("mock", "mock-reviewer", "PRCRITIQ_MODEL_POLICY=mock was requested")
    if policy == "anthropic":
        return ModelChoice(
            "anthropic", settings.anthropic_model, "PRCRITIQ_MODEL_POLICY=anthropic was requested"
        )
    if policy == "openai":
        return ModelChoice(
            "openai", settings.openai_model, "PRCRITIQ_MODEL_POLICY=openai was requested"
        )
    if policy != "auto":
        raise ProviderError(
            f"PRCRITIQ_MODEL_POLICY must be one of: auto, anthropic, openai, mock. Got {policy!r}."
        )

    if task == "batch_eval":
        return ModelChoice(
            "openai", settings.openai_model, "batch evaluation prefers the cost-heavy provider"
        )
    if prompt_characters > LARGE_PROMPT_CHARACTERS:
        return ModelChoice(
            "openai",
            settings.openai_model,
            f"prompt of {prompt_characters} characters exceeds the compact threshold "
            f"of {LARGE_PROMPT_CHARACTERS}",
        )
    return ModelChoice(
        "anthropic",
        settings.anthropic_model,
        "compact, judgment-heavy review synthesis prefers Claude",
    )


class MockProvider:
    """Returns canned findings so CI never needs a live model (NFR4)."""

    def __init__(self, response: DraftedFindings | None = None) -> None:
        self.response = response or DraftedFindings()
        self.calls: list[tuple[str, str, ModelChoice]] = []

    def draft(self, *, system: str, user: str, choice: ModelChoice) -> DraftedFindings:
        self.calls.append((system, user, choice))
        return self.response


class AnthropicProvider:
    """Claude, through the official SDK with a structured output contract."""

    def __init__(self, api_key: str | None) -> None:
        if not api_key:
            raise ProviderError(
                "The router selected Anthropic but ANTHROPIC_API_KEY is not set. "
                "Set the key, or select another provider with PRCRITIQ_MODEL_POLICY."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProviderError("The anthropic package is not installed") from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self._errors = anthropic

    def draft(self, *, system: str, user: str, choice: ModelChoice) -> DraftedFindings:
        try:
            response = self._client.messages.parse(
                model=choice.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=DraftedFindings,
                thinking={"type": "adaptive"},
            )
        except self._errors.APIStatusError as exc:
            raise ProviderError(
                f"Anthropic rejected the review request ({exc.status_code}): {exc.message}"
            ) from exc
        except self._errors.APIConnectionError as exc:
            raise ProviderError(f"Could not reach Anthropic: {exc}") from exc

        if response.stop_reason == "refusal":
            raise ProviderError("Anthropic declined the review request")
        parsed = response.parsed_output
        if parsed is None:
            raise ProviderError("Anthropic returned no parsable findings payload")
        return parsed


class OpenAIProvider:
    """OpenAI, constrained to a JSON object and validated locally."""

    def __init__(self, api_key: str | None) -> None:
        if not api_key:
            raise ProviderError(
                "The router selected OpenAI but OPENAI_API_KEY is not set. "
                "Set the key, or select another provider with PRCRITIQ_MODEL_POLICY."
            )
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProviderError("The openai package is not installed") from exc
        self._client = openai.OpenAI(api_key=api_key)
        self._errors = openai

    def draft(self, *, system: str, user: str, choice: ModelChoice) -> DraftedFindings:
        try:
            response = self._client.chat.completions.create(
                model=choice.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except self._errors.APIStatusError as exc:
            raise ProviderError(f"OpenAI rejected the review request: {exc}") from exc
        except self._errors.APIConnectionError as exc:
            raise ProviderError(f"Could not reach OpenAI: {exc}") from exc

        payload = response.choices[0].message.content or ""
        try:
            return DraftedFindings.model_validate(json.loads(payload))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError(
                f"OpenAI returned findings that do not match the schema: {exc}"
            ) from exc


def build_provider(choice: ModelChoice, settings: Settings) -> ModelProvider:
    """Construct exactly the provider the router chose, or raise."""

    if choice.provider == "mock":
        return MockProvider()
    if choice.provider == "anthropic":
        return AnthropicProvider(settings.anthropic_api_key)
    if choice.provider == "openai":
        return OpenAIProvider(settings.openai_api_key)
    raise ProviderError(f"Unknown provider: {choice.provider}")
