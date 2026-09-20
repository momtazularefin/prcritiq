"""Opt-in semantic replay of frozen candidates; never publishes or drafts findings."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from .benchmark import certify_dataset, load_case_inputs
from .config import Settings
from .dataset import BenchmarkCase, read_dataset
from .diff import FileDiff, build_diff_index, file_diff_from_changed_file
from .findings import CandidateFinding, ReviewedFinding
from .github import GitHubClient
from .guardrails import apply_guardrails, reviewable_files
from .providers import ModelChoice, ProviderBillingError, ProviderError
from .verification import (
    OpenAIVerifier,
    SemanticVerifier,
    build_verification_prompt,
    validate_verification,
)
from .verification_context import build_candidate_context
from .verification_sources import fetch_sources, validate_source_request

_MAX_FILE_BYTES = 250_000
_MAX_TOTAL_BYTES = 2_000_000
_MAX_JSON_BYTES = 32_000_000
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class ReplayError(ValueError):
    """Preflight failed before any model call."""


@dataclass(frozen=True)
class _Case:
    case: BenchmarkCase
    diffs: tuple[FileDiff, ...]
    head_paths: tuple[str, ...]
    base_paths: tuple[str, ...]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReplayError("Duplicate JSON key in replay input")
        result[key] = value
    return result


def _read_json(path: Path) -> tuple[dict, str]:
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise ReplayError("Replay input exceeds the 32 MB file limit")
    raw = path.read_bytes()
    data = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    if not isinstance(data, dict):
        raise ReplayError("Replay input must be a JSON object")
    return data, _digest(raw)


def _prepare_cases(dataset_path: Path, root: Path, settings: Settings) -> dict[str, _Case]:
    cases = read_dataset(dataset_path)
    if len({case.id for case in cases}) != len(cases):
        raise ReplayError("Duplicate dataset case IDs")
    if not cases or len(cases) > 100:
        raise ReplayError("Replay requires between 1 and 100 dataset cases")
    for case in cases:
        fixture = (root / case.diff_path).resolve()
        if not fixture.is_relative_to(root.resolve()):
            raise ReplayError("Dataset fixture must be inside the project root")
    certification = certify_dataset(cases, root)
    if not certification.certified:
        raise ReplayError(
            "Refusing verification: dataset is not certified; no provider was called. "
            + "; ".join(certification.issues)
        )
    prepared = {}
    for case in cases:
        _, changed = load_case_inputs(case, root)
        diffs = tuple(file_diff_from_changed_file(item) for item in changed)
        if len({item.path for item in diffs}) != len(diffs):
            raise ReplayError("Duplicate changed file paths in fixture")
        eligible = reviewable_files(diffs, apply_guardrails(diffs, settings))
        head_paths = tuple(item.path for item in eligible if item.status != "removed")
        base_paths = tuple(
            item.previous_path or item.path for item in eligible if item.status != "added"
        )
        # Validate ALL cases, revisions and paths before the first network request.
        validate_source_request(
            case.repo, case.head_sha, head_paths, _MAX_FILE_BYTES, _MAX_TOTAL_BYTES
        )
        validate_source_request(
            case.repo, case.base_sha, base_paths, _MAX_FILE_BYTES, _MAX_TOTAL_BYTES
        )
        prepared[case.id] = _Case(case, eligible, head_paths, base_paths)
    return prepared


def _prepare_candidates(report: dict, cases: dict[str, _Case]) -> list[dict]:
    report_cases = report.get("cases")
    if not isinstance(report_cases, list) or not report_cases:
        raise ReplayError("Candidate report must contain a nonempty cases list")
    if report.get("aborted"):
        raise ReplayError("Cannot replay an aborted generation report")
    rows, seen = [], set()
    for entry in report_cases:
        if not isinstance(entry, dict) or not isinstance(entry.get("case_id"), str):
            raise ReplayError("Malformed candidate report case")
        case_id = entry["case_id"]
        if case_id in seen or case_id not in cases:
            raise ReplayError("Duplicate or unknown report case ID")
        seen.add(case_id)
        case = cases[case_id].case
        if entry.get("repo") != case.repo or entry.get("pr_number") != case.pr_number:
            raise ReplayError("Report pull request does not match the dataset")
        if entry.get("error"):
            raise ReplayError("Cannot replay a failed generation case")
        for field, expected in (("base_sha", case.base_sha), ("head_sha", case.head_sha)):
            if field in entry and entry[field] != expected:
                raise ReplayError("Report revision does not match the dataset")
        for bucket, decision in (("published", "publish"), ("suppressed", "suppress")):
            findings = entry.get(f"{bucket}_findings")
            if not isinstance(findings, list):
                raise ReplayError("Report must retain published and suppressed candidate lists")
            for ordinal, item in enumerate(findings):
                if not isinstance(item, dict) or not isinstance(item.get("candidate"), dict):
                    raise ReplayError("Malformed saved candidate")
                raw = item["candidate"]
                if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > 32_000:
                    raise ReplayError("Saved candidate exceeds the 32 KB input limit")
                if set(raw) - set(CandidateFinding.model_fields):
                    raise ReplayError("Unknown saved candidate fields")
                candidate = CandidateFinding.model_validate_json(json.dumps(raw), strict=True)
                reviewed = ReviewedFinding.model_validate(item)
                if reviewed.publish_decision != decision:
                    raise ReplayError("Candidate provenance disagrees with report bucket")
                if not candidate.finding.strip():
                    raise ReplayError("Saved candidate allegation must not be blank")
                identity = json.dumps([case_id, bucket, ordinal, raw], sort_keys=True).encode()
                rows.append(
                    {
                        "candidate_id": _digest(identity),
                        "case_id": case_id,
                        "origin": bucket,
                        "original_index": item.get("finding_index", ordinal),
                        "suppression_reason": item.get("suppression_reason"),
                        "candidate": raw,
                        "status": "pending",
                    }
                )
    if len(rows) > 10_000:
        raise ReplayError("Report exceeds the 10,000 candidate limit")
    return rows


def _source_bundle(payload: dict, cases: dict[str, _Case]) -> dict[str, dict]:
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise ReplayError("Unsupported source bundle schema")
    bundles = {}
    for entry in payload["cases"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("case_id"), str):
            raise ReplayError("Malformed source bundle case")
        case_id = entry["case_id"]
        if case_id not in cases or case_id in bundles:
            raise ReplayError("Unknown or duplicate source bundle case")
        prepared = cases[case_id]
        case = prepared.case
        if any(
            entry.get(field) != getattr(case, field) for field in ("repo", "base_sha", "head_sha")
        ):
            raise ReplayError("Source bundle identity does not match the certified dataset")
        for side, allowed in (("base", prepared.base_paths), ("head", prepared.head_paths)):
            sources = entry.get(side)
            if not isinstance(sources, dict) or set(sources) - set(allowed):
                raise ReplayError("Source bundle contains out-of-scope paths")
            total = 0
            for source in sources.values():
                if not isinstance(source, str):
                    raise ReplayError("Source bundle content must be text")
                size = len(source.encode("utf-8"))
                if size > _MAX_FILE_BYTES:
                    raise ReplayError("Source file exceeds byte limit")
                total += size
            if total > _MAX_TOTAL_BYTES:
                raise ReplayError("Source bundle exceeds per-revision byte limit")
        _check_patch_sources(prepared.diffs, entry)
        bundles[case_id] = entry
    return bundles


def _check_patch_sources(diffs: tuple[FileDiff, ...], bundle: dict) -> None:
    """Check every available patch line, not just the candidate target.

    This binds visible lines to the frozen diff, not the unseen portions of a
    user-supplied source file. The latter remain operator-trusted, not attested.
    """
    for diff in diffs:
        source_lines = {}
        for side, path in (("base", diff.previous_path or diff.path), ("head", diff.path)):
            text = bundle[side].get(path)
            if text is not None:
                source_lines[side] = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        old = new = None
        for line in (diff.patch or "").splitlines():
            match = _HUNK.match(line)
            if match:
                old, new = map(int, match.groups())
                continue
            if old is None or line.startswith("\\"):
                continue
            prefix, text = (line[:1], line[1:]) if line else (" ", "")
            for side, cursor, included in (
                ("base", old, prefix in {" ", "-"}),
                ("head", new, prefix in {" ", "+"}),
            ):
                if included and side in source_lines:
                    lines = source_lines[side]
                    if cursor < 1 or cursor > len(lines) or lines[cursor - 1] != text:
                        raise ReplayError(f"Source/patch mismatch: {side} {diff.path}:{cursor}")
            old += int(prefix in {" ", "-"})
            new += int(prefix in {" ", "+"})


def _write_report(report: dict, out_dir: Path) -> None:
    report["status_counts"] = dict(Counter(row["status"] for row in report["candidates"]))
    (out_dir / "verification.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_verification_replay(
    *,
    dataset_path: Path,
    report_path: Path,
    out_dir: Path,
    settings: Settings,
    root: Path,
    source_bundle_path: Path | None = None,
    fetch_context: bool = False,
    live: bool = False,
    model: str = "gpt-5.6-sol",
    effort: str = "low",
    max_candidates: int = 20,
    verifier: SemanticVerifier | None = None,
    client: GitHubClient | None = None,
) -> dict[str, Any]:
    """Prepare evidence by default. Only explicit live=True permits model calls.

    Inputs and all source bundles are checked before constructing a verifier.
    Error rows fail closed and abort further calls; already completed results
    are checkpointed after each request. Costs are verification-only estimates.
    """
    if model not in {"gpt-5.6-sol", "gpt-5.6-terra"} or effort not in {"low", "medium"}:
        raise ReplayError("Replay supports explicit Sol/Terra models at low or medium effort")
    if type(max_candidates) is not int or not 1 <= max_candidates <= 100:
        raise ReplayError("max_candidates must be between 1 and 100")
    if bool(source_bundle_path) == bool(fetch_context):
        raise ReplayError("Choose exactly one source bundle or fetch_context")
    if out_dir.exists():
        raise ReplayError("Replay requires a fresh output directory")
    try:
        cases = _prepare_cases(dataset_path, root, settings)
        original, report_hash = _read_json(report_path)
        rows = _prepare_candidates(original, cases)
        bundles = (
            _source_bundle(_read_json(source_bundle_path)[0], cases) if source_bundle_path else {}
        )
    except (OSError, TypeError, KeyError, ValueError) as exc:
        raise ReplayError(f"Replay preflight failed: {exc}") from exc

    # Fail before even a GitHub fetch when a live credential is missing.
    if live and verifier is None and not settings.openai_api_key:
        raise ReplayError("Live verification requires OPENAI_API_KEY")
    if fetch_context:
        owns_client = client is None
        resolved = client or GitHubClient(
            token=settings.github_token,
            api_base_url=settings.github_api_base_url,
            timeout_seconds=settings.github_request_timeout_seconds,
        )
        try:
            for case_id in dict.fromkeys(row["case_id"] for row in rows):
                prepared = cases[case_id]
                case = prepared.case
                entry = {
                    "case_id": case_id,
                    "repo": case.repo,
                    "base_sha": case.base_sha,
                    "head_sha": case.head_sha,
                }
                for side, paths in (("base", prepared.base_paths), ("head", prepared.head_paths)):
                    entry[side] = fetch_sources(
                        client=resolved,
                        repo=case.repo,
                        ref=getattr(case, f"{side}_sha"),
                        paths=paths,
                    )
                _check_patch_sources(prepared.diffs, entry)
                bundles[case_id] = entry
        finally:
            if owns_client:
                resolved.close()

    source_payload = {"schema_version": 1, "cases": list(bundles.values())}
    source_bytes = (
        json.dumps(source_payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    if len(source_bytes) > _MAX_JSON_BYTES:
        raise ReplayError("Prepared source bundle exceeds the 32 MB replay limit")
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "sources.json").write_bytes(source_bytes)
    report = {
        "schema_version": 1,
        "mode": "live" if live else "prepare",
        "model": model,
        "effort": effort,
        "max_candidates": max_candidates,
        "binding": "operator-selected certified dataset; legacy generation revisions not attested",
        "source_provenance": "github-commit-fetch" if fetch_context else "operator-supplied",
        "source_validation": "all available frozen patch lines checked; no Git object attestation",
        "dataset_sha256": _digest(dataset_path.read_bytes()),
        "generation_report_sha256": report_hash,
        "source_bundle_sha256": _digest(source_bytes),
        "fixtures": {
            key: _digest((root / item.case.diff_path).read_bytes()) for key, item in cases.items()
        },
        "generation_metrics": original.get("metrics"),
        "calls": 0,
        "cost_usd": 0.0,
        "usage_complete": True,
        "aborted": None,
        "candidates": rows,
    }
    choice = ModelChoice(provider="openai", model=model, reason="explicit semantic replay")
    _write_report(report, out_dir)
    for row in rows:
        prepared = cases[row["case_id"]]
        case = prepared.case
        candidate = CandidateFinding.model_validate(row["candidate"])
        if report["aborted"]:
            row["status"] = "aborted"
            continue
        index = build_diff_index(prepared.diffs)
        if (
            not index.validate_line(candidate.file_path, candidate.line).valid
            or not candidate.evidence.strip()
        ):
            row["status"] = "structural_reject"
            continue
        bundle = bundles.get(case.id, {"base": {}, "head": {}})
        diff = next(item for item in prepared.diffs if item.path == candidate.file_path)
        context = build_candidate_context(
            candidate,
            base_sources=bundle["base"],
            head_sources=bundle["head"],
            base_sha=case.base_sha,
            head_sha=case.head_sha,
            previous_path=diff.previous_path,
            related_paths=prepared.head_paths,
            base_absent=diff.status == "added",
        )
        row["context"] = asdict(context)
        if context.issues or context.truncated or not context.snippets:
            row["status"] = "context_unavailable"
            continue
        if not live:
            row["status"] = "prepared"
            continue
        if report["calls"] >= max_candidates:
            row["status"] = "budget_exceeded"
            continue
        system, user = build_verification_prompt(
            candidate, context, repo=case.repo, base_sha=case.base_sha, head_sha=case.head_sha
        )
        row["prompt_sha256"] = _digest((system + "\0" + user).encode())
        started = perf_counter()
        try:
            if verifier is None:
                verifier = OpenAIVerifier(settings.openai_api_key, effort=effort)
            report["calls"] += 1
            result = verifier.verify(system=system, user=user, choice=choice)
            row["usage"] = asdict(result.usage)
            row["cost_usd"] = result.usage.cost_usd(model)
            report["cost_usd"] += row["cost_usd"]
            row["decision"] = result.decision.model_dump(mode="json")
            invalid = validate_verification(result.decision, context)
            row["validation_error"] = invalid
            row["status"] = "invalid_decision" if invalid else result.decision.verdict
        except ProviderError as exc:
            # Never persist remote exception bodies: they can echo source or credentials.
            reason = "billing_error" if isinstance(exc, ProviderBillingError) else "provider_error"
            row["status"] = "provider_error"
            row["error"] = reason
            report["aborted"] = reason
            known_usage = getattr(exc, "usage", None)
            if known_usage is None:
                report["usage_complete"] = False
            else:
                row["usage"] = asdict(known_usage)
                row["cost_usd"] = known_usage.cost_usd(model)
                report["cost_usd"] += row["cost_usd"]
        finally:
            row["latency_seconds"] = perf_counter() - started
            _write_report(report, out_dir)
    _write_report(report, out_dir)
    return report
