"""Run the frozen verifier smoke experiment; preparation is the default.

This is an experiment driver, not a production routing policy. Never resume a
partially billed run by rerunning it: output directories must be fresh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from prcritiq.config import load_settings
from prcritiq.dataset import read_dataset
from prcritiq.providers import ProviderError, Usage
from prcritiq.verification import (
    MAX_VERIFICATION_OUTPUT_TOKENS,
    OpenAIVerifier,
    VerificationDecision,
    VerificationProviderError,
)
from prcritiq.verification_replay import run_verification_replay

GENERATORS = ("opus-5-low", "terra-low", "terra-medium", "sol-low")
MODELS = ("gpt-5.6-sol", "gpt-5.6-terra")
PRICES = {"gpt-5.6-sol": (5.0, 20.0), "gpt-5.6-terra": (2.5, 12.0)}


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request_reserve(system: str, user: str, model: str) -> float:
    """Conservative estimate, not a contractual provider billing ceiling.

    Treat every UTF-8 byte as a token, reserve 2,048 framing/schema overhead
    tokens, full cache-write input pricing, and the entire output allowance.
    """
    schema = json.dumps(VerificationDecision.model_json_schema())
    input_allowance = len((system + user + schema).encode("utf-8")) + 2048
    input_price, output_price = PRICES[model]
    return (input_allowance * input_price + MAX_VERIFICATION_OUTPUT_TOKENS * output_price) / 1e6


class BudgetedVerifier:
    def __init__(self, *, delegate, ledger: dict, budget: float, checkpoint) -> None:
        self.delegate = delegate
        self.ledger = ledger
        self.budget = budget
        self.checkpoint = checkpoint

    def verify(self, *, system, user, choice):
        if (
            self.ledger["requests_started"] >= 48
            or self.ledger["stop_reason"]
            or not self.ledger["usage_complete"]
            or self.ledger["in_flight"]
        ):
            self.ledger["stop_reason"] = self.ledger["stop_reason"] or "request_cap_exhausted"
            self.checkpoint()
            raise VerificationProviderError("Experiment stopped", usage=Usage())
        reserve = request_reserve(system, user, choice.model)
        if self.ledger["known_cost_usd"] + reserve > self.budget:
            self.ledger["stop_reason"] = "working_budget_reserve_exhausted"
            self.checkpoint()
            raise VerificationProviderError("Experiment budget reserve exhausted", usage=Usage())
        self.ledger["requests_started"] += 1
        self.ledger["in_flight"] = {
            "model": choice.model,
            "started_at": now(),
            "prompt_sha256": hashlib.sha256((system + "\0" + user).encode()).hexdigest(),
            "reserved_cost_usd": reserve,
        }
        self.checkpoint()
        print(f"Request {self.ledger['requests_started']}/48: {choice.model}", flush=True)
        try:
            result = self.delegate.verify(system=system, user=user, choice=choice)
        except ProviderError as exc:
            usage = getattr(exc, "usage", None)
            if usage is None:
                self.ledger["usage_complete"] = False
            else:
                self.ledger["known_cost_usd"] += usage.cost_usd(choice.model)
            self.ledger["stop_reason"] = "provider_error"
            self.ledger["in_flight"] = None
            self.checkpoint()
            raise
        usage = result.usage
        if (
            usage.cached_input_tokens + usage.cache_write_input_tokens > usage.input_tokens
            or usage.reasoning_output_tokens > usage.output_tokens
        ):
            self.ledger["usage_complete"] = False
            self.ledger["stop_reason"] = "inconsistent_usage"
            self.ledger["in_flight"] = None
            self.checkpoint()
            raise VerificationProviderError("Experiment received inconsistent usage")
        self.ledger["known_cost_usd"] += usage.cost_usd(choice.model)
        self.ledger["in_flight"] = None
        self.checkpoint()
        print(f"  Known cumulative cost: ${self.ledger['known_cost_usd']:.6f}", flush=True)
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source-bundle", required=True, type=Path)
    parser.add_argument(
        "--live", action="store_true", help="Spend API credit; no automatic retries"
    )
    parser.add_argument("--budget-usd", type=float, default=2.0)
    args = parser.parse_args(argv)
    if not 0 < args.budget_usd <= 2.0:
        parser.error("Working budget must be positive and at most $2 for this smoke experiment")
    if args.out.exists():
        parser.error("Use a fresh output directory; never rerun a partially billed experiment")
    root = Path.cwd()
    settings = load_settings()
    if args.live and not settings.openai_api_key:
        parser.error("OPENAI_API_KEY is required")
    dataset = Path("eval/ground-truth-candidates/dataset-certified.jsonl")
    generation_dir = Path("eval/runs/2026-09-18-certified-smoke")
    args.out.mkdir(parents=True, exist_ok=False)
    # Validate every generation report and complete source bundle before any live call.
    prepared = {}
    for generator in GENERATORS:
        prepared[generator] = run_verification_replay(
            dataset_path=dataset,
            report_path=generation_dir / generator / "benchmark.json",
            out_dir=args.out / "preflight" / generator,
            settings=settings,
            root=root,
            source_bundle_path=args.source_bundle,
            max_candidates=12,
        )
    eligible = sum(report["status_counts"].get("prepared", 0) for report in prepared.values())
    if eligible != 24 or sum(len(report["candidates"]) for report in prepared.values()) != 27:
        raise RuntimeError("Frozen experiment changed: expected 27 candidates / 24 eligible")
    files = [
        Path("src/prcritiq") / name
        for name in (
            "verification.py",
            "verification_context.py",
            "verification_replay.py",
            "providers.py",
        )
    ]
    files.append(Path(__file__).resolve())
    manifest = {
        "schema_version": 1,
        "started_at": now(),
        "completed_at": None,
        "status": "prepared",
        "live": args.live,
        "purpose": "Controlled verifier smoke comparison; not a publishable accuracy benchmark",
        "openai_sdk_version": version("openai"),
        "api_origin": "https://api.openai.com/v1",
        "model_identity": "Explicit requested aliases; immutable backend snapshot not attested",
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dataset_sha256": sha(dataset),
        "source_bundle_sha256": sha(args.source_bundle),
        "code_sha256": {
            str(path.relative_to(root) if path.is_absolute() else path): sha(path) for path in files
        },
        "generation_reports": {g: sha(generation_dir / g / "benchmark.json") for g in GENERATORS},
        "protocol": {
            "models": list(MODELS),
            "effort": "low",
            "eligible_candidates_per_model": eligible,
            "max_api_requests": 48,
            "working_budget_usd": args.budget_usd,
            "max_output_tokens": MAX_VERIFICATION_OUTPUT_TOKENS,
            "retries": 0,
            "timeout_seconds": 60,
            "repetitions": 1,
            "schedule": "Sequential generator blocks; first model alternates by block",
            "incomplete_context_candidates_per_model": 3,
            "reserve": (
                "UTF-8 bytes as tokens plus 2048 overhead, cache-write price, full output cap; "
                "conservative estimate, not guaranteed bill"
            ),
        },
        "ledger": {
            "requests_started": 0,
            "known_cost_usd": 0.0,
            "usage_complete": True,
            "in_flight": None,
            "stop_reason": None,
        },
        "runs": [],
    }

    def checkpoint():
        (args.out / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    checkpoint()
    if not args.live:
        print(json.dumps({"status": "prepared", "eligible_per_model": eligible, "calls": 0}))
        return 0
    manifest["status"] = "running"
    frozen_inputs = {
        dataset: manifest["dataset_sha256"],
        args.source_bundle: manifest["source_bundle_sha256"],
        **{
            generation_dir / g / "benchmark.json": manifest["generation_reports"][g]
            for g in GENERATORS
        },
        **{path: sha(path) for path in files},
    }
    frozen_inputs.update(
        {root / case.diff_path: sha(root / case.diff_path) for case in read_dataset(dataset)}
    )
    for index, generator in enumerate(GENERATORS):
        for model in MODELS if index % 2 == 0 else tuple(reversed(MODELS)):
            if any(sha(path) != expected for path, expected in frozen_inputs.items()):
                manifest["status"] = "aborted"
                manifest["ledger"]["stop_reason"] = "frozen_input_changed"
                checkpoint()
                raise RuntimeError("Frozen experiment input changed; refusing further calls")
            name = f"{generator}--{model.removeprefix('gpt-5.6-')}-low"
            run = {"name": name, "generation_arm": generator, "model": model, "started_at": now()}
            manifest["runs"].append(run)
            checkpoint()
            delegate = OpenAIVerifier(settings.openai_api_key, effort="low")
            if str(delegate._client.base_url).rstrip("/") != "https://api.openai.com/v1":
                raise RuntimeError("Experiment requires the official OpenAI API origin")
            verifier = BudgetedVerifier(
                delegate=delegate,
                ledger=manifest["ledger"],
                budget=args.budget_usd,
                checkpoint=checkpoint,
            )
            report = run_verification_replay(
                dataset_path=dataset,
                report_path=generation_dir / generator / "benchmark.json",
                out_dir=args.out / name,
                settings=settings,
                root=root,
                source_bundle_path=args.source_bundle,
                live=True,
                model=model,
                effort="low",
                max_candidates=12,
                verifier=verifier,
            )
            run.update(
                {
                    "completed_at": now(),
                    "status_counts": report["status_counts"],
                    "calls": report["calls"],
                    "cost_usd": report["cost_usd"],
                    "report_sha256": sha(args.out / name / "verification.json"),
                    "aborted": report["aborted"],
                }
            )
            if report["aborted"]:
                manifest["status"] = "aborted"
                manifest["completed_at"] = now()
                checkpoint()
                return 1
            checkpoint()
    manifest["status"] = "completed"
    manifest["completed_at"] = now()
    checkpoint()
    print(json.dumps(manifest["ledger"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
