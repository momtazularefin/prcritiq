"""Experiment-wide request/spend safeguards, using only in-memory delegates."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from prcritiq.providers import ModelChoice, ProviderBillingError, ProviderError, Usage
from prcritiq.verification import VerificationDecision, VerificationProviderError

_DRIVER_PATH = Path(__file__).resolve().parents[1] / "eval" / "run_verifier_comparison.py"
_SPEC = importlib.util.spec_from_file_location("verifier_comparison_under_test", _DRIVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
driver = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(driver)

CHOICE = ModelChoice("openai", "gpt-5.6-sol", "test only")


@pytest.fixture
def ledger():
    return {
        "requests_started": 0,
        "known_cost_usd": 0.0,
        "usage_complete": True,
        "in_flight": None,
        "stop_reason": None,
    }


class Delegate:
    def __init__(self, *, usage=None, error=None, inspect=None):
        self.usage = Usage(input_tokens=100, output_tokens=20) if usage is None else usage
        self.error = error
        self.inspect = inspect
        self.calls = []

    def verify(self, **kwargs):
        self.calls.append(kwargs)
        if self.inspect is not None:
            self.inspect()
        if self.error is not None:
            raise self.error
        return SimpleNamespace(usage=self.usage)


def wrapper(ledger, delegate, *, budget=2.0):
    checkpoints = []
    instance = driver.BudgetedVerifier(
        delegate=delegate,
        ledger=ledger,
        budget=budget,
        checkpoint=lambda: checkpoints.append(deepcopy(ledger)),
    )
    return instance, checkpoints


@pytest.mark.parametrize(
    "model,input_price,output_price",
    [
        ("gpt-5.6-sol", 5.0, 20.0),
        ("gpt-5.6-terra", 2.5, 12.0),
    ],
)
def test_reserve_includes_utf8_prompt_schema_overhead_and_full_output_cap(
    model, input_price, output_price
) -> None:
    system, user = "Rules ☃", "বাংলা source"
    schema = json.dumps(VerificationDecision.model_json_schema())
    input_allowance = len((system + user + schema).encode("utf-8")) + 2048
    assert driver.MAX_VERIFICATION_OUTPUT_TOKENS == 8192
    expected = (input_allowance * input_price + 8192 * output_price) / 1_000_000
    assert driver.request_reserve(system, user, model) == pytest.approx(expected)


def test_reserve_grows_by_bytes_not_unicode_character_count() -> None:
    plain = driver.request_reserve("", "a", CHOICE.model)
    unicode = driver.request_reserve("", "☃", CHOICE.model)
    assert unicode - plain == pytest.approx((3 - 1) * 5.0 / 1_000_000)


def test_unpriced_model_cannot_create_zero_reservation() -> None:
    with pytest.raises(KeyError):
        driver.request_reserve("", "", "unknown-model")


def test_reserve_exhaustion_refuses_before_delegate_and_records_zero_usage(ledger) -> None:
    delegate = Delegate()
    reserve = driver.request_reserve("rules", "data", CHOICE.model)
    ledger["known_cost_usd"] = 0.25
    instance, checkpoints = wrapper(ledger, delegate, budget=0.25 + reserve - 0.000001)

    with pytest.raises(VerificationProviderError) as caught:
        instance.verify(system="rules", user="data", choice=CHOICE)

    assert not delegate.calls
    assert ledger["requests_started"] == 0
    assert ledger["known_cost_usd"] == 0.25
    assert ledger["usage_complete"] is True
    assert ledger["in_flight"] is None
    assert ledger["stop_reason"] == "working_budget_reserve_exhausted"
    assert caught.value.usage == Usage()
    assert checkpoints[-1] == ledger


def test_exact_reservation_boundary_can_dispatch_and_account_actual_cost(ledger) -> None:
    reserve = driver.request_reserve("rules", "data", CHOICE.model)
    usage = Usage(
        input_tokens=1000,
        output_tokens=100,
        cached_input_tokens=200,
        cache_write_input_tokens=100,
        reasoning_output_tokens=50,
    )
    delegate = Delegate(usage=usage)
    instance, checkpoints = wrapper(ledger, delegate, budget=reserve)

    result = instance.verify(system="rules", user="data", choice=CHOICE)

    assert result.usage == usage
    assert len(delegate.calls) == 1
    assert ledger["requests_started"] == 1
    assert ledger["known_cost_usd"] == pytest.approx(usage.cost_usd(CHOICE.model))
    assert ledger["usage_complete"] is True
    assert ledger["stop_reason"] is None
    assert ledger["in_flight"] is None
    assert checkpoints[-1] == ledger


def test_attempt_and_prompt_reservation_are_checkpointed_before_dispatch(ledger) -> None:
    def inspect():
        assert ledger["requests_started"] == 1
        assert ledger["in_flight"]["model"] == CHOICE.model
        assert ledger["in_flight"]["reserved_cost_usd"] > 0
        assert checkpoints[-1] == ledger

    delegate = Delegate(inspect=inspect)
    instance, checkpoints = wrapper(ledger, delegate)
    instance.verify(system="rules", user="data", choice=CHOICE)

    assert (
        checkpoints[0]["in_flight"]["prompt_sha256"] == hashlib.sha256(b"rules\0data").hexdigest()
    )
    assert checkpoints[0]["in_flight"]["started_at"]
    assert checkpoints[-1]["in_flight"] is None


def test_known_usage_on_failed_request_is_charged_and_stops_shared_ledger(ledger) -> None:
    usage = Usage(input_tokens=1000, output_tokens=400)
    error = VerificationProviderError("billable invalid decision", usage=usage)
    delegate = Delegate(error=error)
    instance, checkpoints = wrapper(ledger, delegate)

    with pytest.raises(VerificationProviderError) as caught:
        instance.verify(system="rules", user="data", choice=CHOICE)

    assert caught.value is error
    assert len(delegate.calls) == 1
    assert ledger["requests_started"] == 1
    assert ledger["known_cost_usd"] == pytest.approx(usage.cost_usd(CHOICE.model))
    assert ledger["usage_complete"] is True
    assert ledger["stop_reason"] == "provider_error"
    assert ledger["in_flight"] is None
    assert checkpoints[-1] == ledger


@pytest.mark.parametrize(
    "error",
    [
        ProviderError("transport failed"),
        VerificationProviderError("missing counters"),
        ProviderBillingError("quota exhausted"),
    ],
)
def test_unknown_usage_failure_marks_unknown_and_never_retries(ledger, error) -> None:
    ledger["known_cost_usd"] = 0.25
    delegate = Delegate(error=error)
    instance, checkpoints = wrapper(ledger, delegate)

    with pytest.raises(ProviderError) as caught:
        instance.verify(system="rules", user="data", choice=CHOICE)

    assert caught.value is error
    assert len(delegate.calls) == 1
    assert ledger["requests_started"] == 1
    assert ledger["known_cost_usd"] == 0.25
    assert ledger["usage_complete"] is False
    assert ledger["stop_reason"] == "provider_error"
    assert ledger["in_flight"] is None
    assert checkpoints[-1] == ledger


def test_new_model_wrapper_cannot_resume_a_stopped_shared_ledger(ledger) -> None:
    first = Delegate(error=VerificationProviderError("unknown usage"))
    instance, _ = wrapper(ledger, first)
    with pytest.raises(ProviderError):
        instance.verify(system="rules", user="data", choice=CHOICE)
    second = Delegate()
    next_instance, _ = wrapper(ledger, second)
    other_choice = ModelChoice("openai", "gpt-5.6-terra", "same experiment")

    with pytest.raises(ProviderError):
        next_instance.verify(system="rules", user="data", choice=other_choice)

    assert not second.calls
    assert ledger["requests_started"] == 1
    assert ledger["usage_complete"] is False


def test_48_request_cap_is_shared_across_model_wrappers(ledger) -> None:
    ledger["requests_started"] = 47
    first = Delegate()
    instance, _ = wrapper(ledger, first)
    instance.verify(system="rules", user="data", choice=CHOICE)
    assert ledger["requests_started"] == 48
    second = Delegate()
    next_instance, checkpoints = wrapper(ledger, second)

    with pytest.raises(ProviderError):
        next_instance.verify(
            system="rules",
            user="data",
            choice=ModelChoice("openai", "gpt-5.6-terra", "same experiment"),
        )

    assert len(first.calls) == 1
    assert not second.calls
    assert ledger["requests_started"] == 48
    assert ledger["stop_reason"] is not None
    assert checkpoints[-1] == ledger


def test_actual_cost_accumulates_across_models_not_just_current_block(ledger) -> None:
    usage = Usage(input_tokens=1000, output_tokens=100)
    first, _ = wrapper(ledger, Delegate(usage=usage))
    first.verify(system="rules", user="data", choice=CHOICE)
    other_choice = ModelChoice("openai", "gpt-5.6-terra", "same experiment")
    second, _ = wrapper(ledger, Delegate(usage=usage))
    second.verify(system="rules", user="data", choice=other_choice)

    assert ledger["requests_started"] == 2
    assert ledger["known_cost_usd"] == pytest.approx(
        usage.cost_usd(CHOICE.model) + usage.cost_usd(other_choice.model)
    )
