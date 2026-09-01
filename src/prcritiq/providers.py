"""Deterministic model routing with no silent fallback.

The routing decision is recorded so every benchmark case can be attributed to
the provider, model, and reason that actually produced it.

Nothing here falls back. A missing provider, model, or key raises, because a run
that quietly used a different model than the one it reports would corrupt every
measurement taken from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol

from .config import Settings
from .findings import DraftedFindings

ProviderName = Literal["anthropic", "openai", "mock"]

#: Above this estimate the prompt stops being "compact" and routing prefers the
#: extensive-context provider. Characters, not tokens: the router must decide
#: before a tokenizer is available, and the ratio is stable enough for a policy.
LARGE_PROMPT_CHARACTERS = 120_000

#: Output allowance per review call. Adaptive thinking draws on the same
#: budget as the answer, so this must clear both.
MAX_OUTPUT_TOKENS = 64_000


class ProviderError(RuntimeError):
    """Raised when a provider cannot be used exactly as configured."""


class ProviderBillingError(ProviderError):
    """Raised when the account cannot pay for the call.

    Separate from other provider failures because it does not vary by case:
    once credit is exhausted every remaining call fails the same way, so a
    batch should stop rather than record the same error twenty times.
    """


@dataclass(frozen=True)
class ModelChoice:
    """The routing decision for one call, recorded alongside the run."""

    provider: ProviderName
    model: str
    reason: str


#: Published per-million-token prices, used only to estimate benchmark cost.
#: Prices drift; a report states the model id so a reader can re-price it.
_PRICES_PER_MTOK: Final[dict[str, tuple[float, float, float]]] = {
    # (uncached input, cached input, output).  Anthropic cache usage is not yet
    # collected, so its cached price intentionally equals ordinary input.
    "claude-opus-5": (5.00, 5.00, 25.00),
    "claude-sonnet-5": (3.00, 3.00, 15.00),
    "claude-haiku-4-5": (1.00, 1.00, 5.00),
    "gpt-5.6": (4.00, 0.40, 20.00),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
}


@dataclass(frozen=True)
class Usage:
    """Token usage for one model call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_output_tokens: int = 0

    def cost_usd(self, model: str) -> float:
        """Estimated cost, or zero for a model with no published price here."""

        prices = _PRICES_PER_MTOK.get(model)
        if prices is None:
            return 0.0
        cached = min(max(self.cached_input_tokens, 0), self.input_tokens)
        uncached = self.input_tokens - cached
        return (
            uncached * prices[0] + cached * prices[1] + self.output_tokens * prices[2]
        ) / 1_000_000


@dataclass(frozen=True)
class DraftResult:
    """What a provider returned, and what it cost to get."""

    findings: DraftedFindings
    usage: Usage = Usage()


class ModelProvider(Protocol):
    """Drafts findings from a system prompt and a user prompt."""

    def draft(self, *, system: str, user: str, choice: ModelChoice) -> DraftResult: ...


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
            "openai",
            settings.openai_model,
            "batch evaluation uses the configured OpenAI comparison model",
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

    def __init__(self, response: DraftedFindings | None = None, usage: Usage | None = None) -> None:
        self.response = response or DraftedFindings()
        self.usage = usage or Usage()
        self.calls: list[tuple[str, str, ModelChoice]] = []

    def draft(self, *, system: str, user: str, choice: ModelChoice) -> DraftResult:
        self.calls.append((system, user, choice))
        return DraftResult(findings=self.response, usage=self.usage)


class AnthropicProvider:
    """Claude, through the official SDK with a structured output contract."""

    #: Effort levels the API accepts. An unknown value is refused rather than
    #: silently dropped, which would bill high-effort work for a low-effort run.
    EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

    def __init__(
        self,
        api_key: str | None,
        workspace_id: str | None = None,
        effort: str = "low",
    ) -> None:
        if not api_key:
            raise ProviderError(
                "The router selected Anthropic but ANTHROPIC_API_KEY is not set. "
                "Set the key, or select another provider with PRCRITIQ_MODEL_POLICY."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProviderError("The anthropic package is not installed") from exc
        # An identity-linked key must name the workspace it acts in; the API
        # rejects the request outright without it.
        if effort not in self.EFFORTS:
            raise ProviderError(
                f"PRCRITIQ_MODEL_EFFORT must be one of: {', '.join(sorted(self.EFFORTS))}. "
                f"Got {effort!r}."
            )
        self._effort = effort
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        self._client = anthropic.Anthropic(api_key=api_key, default_headers=headers)
        self._errors = anthropic

    def draft(self, *, system: str, user: str, choice: ModelChoice) -> DraftResult:
        try:
            # Streamed with a large budget because adaptive thinking spends the
            # same allowance as the answer. At 16000 non-streamed, large diffs
            # truncated mid-structure and the parsed payload came back empty,
            # which read as "the model found nothing" rather than as a failure.
            with self._client.messages.stream(
                model=choice.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=DraftedFindings,
                thinking={"type": "adaptive"},
                # The SDK merges the schema into output_config, so effort and the
                # structured format coexist. Reviewing a bounded diff does not
                # need the default high effort, and thinking was 88 percent of
                # the first run's cost.
                output_config={"effort": self._effort},
            ) as stream:
                response = stream.get_final_message()
        except self._errors.APIStatusError as exc:
            if "credit balance is too low" in str(exc):
                raise ProviderBillingError(
                    "Anthropic reports the credit balance is too low to serve this request."
                ) from exc
            hint = ""
            if "anthropic-workspace-id" in str(exc):
                hint = (
                    " This key is identity-linked: set ANTHROPIC_WORKSPACE_ID to the "
                    "workspace the request should act in."
                )
            raise ProviderError(
                f"Anthropic rejected the review request ({exc.status_code}): {exc.message}{hint}"
            ) from exc
        except self._errors.APIConnectionError as exc:
            raise ProviderError(f"Could not reach Anthropic: {exc}") from exc

        if response.stop_reason == "refusal":
            raise ProviderError("Anthropic declined the review request")
        if response.stop_reason == "max_tokens":
            raise ProviderError(
                f"Anthropic hit the {MAX_OUTPUT_TOKENS}-token output cap before finishing, "
                "so the findings payload is incomplete. Raise the cap or reduce the diff."
            )
        parsed = response.parsed_output
        if parsed is None:
            raise ProviderError("Anthropic returned no parsable findings payload")
        return DraftResult(
            findings=parsed,
            usage=Usage(
                input_tokens=int(getattr(response.usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(response.usage, "output_tokens", 0) or 0),
            ),
        )


class OpenAIProvider:
    """OpenAI Responses API with native typed structured output."""

    EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})

    def __init__(self, api_key: str | None, effort: str = "low") -> None:
        if not api_key:
            raise ProviderError(
                "The router selected OpenAI but OPENAI_API_KEY is not set. "
                "Set the key, or select another provider with PRCRITIQ_MODEL_POLICY."
            )
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProviderError("The openai package is not installed") from exc
        if effort not in self.EFFORTS:
            raise ProviderError(
                f"PRCRITIQ_MODEL_EFFORT must be one of: {', '.join(sorted(self.EFFORTS))}. "
                f"Got {effort!r}."
            )
        self._effort = effort
        self._client = openai.OpenAI(api_key=api_key)
        self._errors = openai

    def draft(self, *, system: str, user: str, choice: ModelChoice) -> DraftResult:
        try:
            response = self._client.responses.parse(
                model=choice.model,
                instructions=system,
                input=user,
                text_format=DraftedFindings,
                reasoning={"effort": self._effort, "context": "current_turn"},
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
        except self._errors.APIStatusError as exc:
            body = getattr(exc, "body", None)
            detail = body.get("error", body) if isinstance(body, dict) else {}
            code = str(detail.get("code") or "") if isinstance(detail, dict) else ""
            if getattr(exc, "status_code", None) == 402 or code in {
                "insufficient_quota",
                "billing_hard_limit_reached",
            }:
                raise ProviderBillingError(
                    "OpenAI reports that this project has no available API quota."
                ) from exc
            raise ProviderError(
                f"OpenAI rejected the review request ({exc.status_code}): {exc}"
            ) from exc
        except self._errors.APIConnectionError as exc:
            raise ProviderError(f"Could not reach OpenAI: {exc}") from exc

        if getattr(response, "status", None) == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
            raise ProviderError(f"OpenAI returned an incomplete response: {reason}")

        parsed = response.output_parsed
        if parsed is None:
            raise ProviderError("OpenAI returned no parsable findings payload")

        response_usage = getattr(response, "usage", None)
        input_details = getattr(response_usage, "input_tokens_details", None)
        output_details = getattr(response_usage, "output_tokens_details", None)
        usage = Usage(
            input_tokens=int(getattr(response_usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(response_usage, "output_tokens", 0) or 0),
            cached_input_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
            reasoning_output_tokens=int(getattr(output_details, "reasoning_tokens", 0) or 0),
        )
        return DraftResult(findings=parsed, usage=usage)


def build_provider(choice: ModelChoice, settings: Settings) -> ModelProvider:
    """Construct exactly the provider the router chose, or raise."""

    if choice.provider == "mock":
        return MockProvider()
    if choice.provider == "anthropic":
        return AnthropicProvider(
            settings.anthropic_api_key,
            settings.anthropic_workspace_id,
            settings.model_effort,
        )
    if choice.provider == "openai":
        return OpenAIProvider(settings.openai_api_key, settings.model_effort)
    raise ProviderError(f"Unknown provider: {choice.provider}")
