"""Validate and summarize the retained September verifier experiment, offline only.

Raw outputs are evidence, never rewritten by this script. The blind assessments
are AI judgments, not new human ground truth. No provider is imported or called.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from statistics import median

MODELS = ("gpt-5.6-sol", "gpt-5.6-terra")
ARMS = ("opus-5-low", "terra-low", "terra-medium", "sol-low")
TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
# Frozen experiment pricing, not a resolver for current pricing.
PRICES = {MODELS[0]: (4, 0.4, 5, 20), MODELS[1]: (2, 0.2, 2.5, 12)}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def candidate_key(arm, item):
    return arm, item["case_id"], item["origin"], item["original_index"]


def summarize(directory: Path) -> dict:
    manifest = read(directory / "manifest.json")
    audit = read(directory / "blind-audit.json")
    require(manifest["status"] == "completed" and manifest["live"], "Incomplete experiment")
    ledger = manifest["ledger"]
    require(
        ledger["usage_complete"] and not ledger["in_flight"] and not ledger["stop_reason"],
        "Incomplete billing ledger",
    )
    require(sha(directory / "sources.json") == manifest["source_bundle_sha256"], "Source hash")
    dataset_path = directory / "dataset-certified.jsonl"
    require(sha(dataset_path) == manifest["dataset_sha256"], "Dataset hash")
    dataset = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines()]
    approved = {
        (case["id"], label["label_id"])
        for case in dataset
        for label in case["labels"]
        if label["human_approved"] is True and label["adjudication"] == "confirmed_defect"
    }
    all_labels = {label for _, label in approved}
    require(
        len(dataset) == 3 and len(approved) == len(all_labels) == 5,
        "Expected three cases / five approved defects",
    )
    require(len(manifest["runs"]) == 8, "Expected eight runs")
    lookup = {
        (c["arm"], c["case_id"], c["bucket"], c["finding_index"]): c for c in audit["candidates"]
    }
    require(len(lookup) == len(audit["candidates"]) == 27, "Duplicate/missing audit candidates")
    for c in lookup.values():
        require(c["human_approved"] is False, "AI assessment must not become human ground truth")
        require(
            c["assessment"] in {"supported", "partial", "unsupported", "uncertain"},
            "Unknown audit assessment",
        )
        require(
            (c["approved_label_id"] is None and c["label_coverage"] is None)
            or (
                (c["case_id"], c["approved_label_id"]) in approved
                and c["label_coverage"] in {"full", "partial"}
            ),
            "Unknown label mapping",
        )
    results = {model: {} for model in MODELS}
    seen_runs = set()
    fixture_hashes = None
    for run in manifest["runs"]:
        arm, model = run["generation_arm"], run["model"]
        require(arm in ARMS and model in MODELS, "Unexpected arm/model")
        require((arm, model) not in seen_runs, "Duplicate run")
        seen_runs.add((arm, model))
        expected_name = f"{arm}--{model.removeprefix('gpt-5.6-')}-low"
        require(run["name"] == expected_name, "Unexpected run name")
        path = directory / "raw" / expected_name / "verification.json"
        require(sha(path) == run["report_sha256"], f"Report hash: {expected_name}")
        report = read(path)
        require(report["model"] == model and report["effort"] == "low", "Model/effort")
        require(report["usage_complete"] and not report["aborted"], "Incomplete report")
        for field in ("dataset_sha256", "source_bundle_sha256"):
            require(report[field] == manifest[field], f"Binding: {field}")
        require(
            report["generation_report_sha256"] == manifest["generation_reports"][arm],
            "Generation binding",
        )
        fixture_hashes = fixture_hashes or report["fixtures"]
        require(report["fixtures"] == fixture_hashes, "Fixture binding")
        counts = Counter(c["status"] for c in report["candidates"])
        require(dict(counts) == report["status_counts"] == run["status_counts"], "Status counts")
        calls = 0
        cost = 0.0
        for c in report["candidates"]:
            require(
                isinstance(c.get("candidate"), dict) and isinstance(c.get("context"), dict),
                "Missing candidate/context",
            )
            key = candidate_key(arm, c)
            require(key in lookup and key not in results[model], "Candidate join/duplicate")
            require(c["candidate_id"] == lookup[key]["candidate_id"], "Audit candidate ID")
            require(
                c["status"] in {"confirmed", "rejected", "uncertain", "context_unavailable"},
                "Unexpected status",
            )
            results[model][key] = c
            if c["status"] == "context_unavailable":
                require("usage" not in c and "decision" not in c, "Unavailable billed/decided")
                continue
            require(c["decision"]["verdict"] == c["status"], "Decision/status mismatch")
            require(
                isinstance(c.get("prompt_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", c["prompt_sha256"]),
                "Missing/invalid prompt hash",
            )
            require(not c["validation_error"], "Invalid decision")
            usage = c["usage"]
            require(
                all(type(usage[t]) is int and usage[t] >= 0 for t in TOKEN_FIELDS), "Invalid usage"
            )
            uncached = (
                usage["input_tokens"]
                - usage["cached_input_tokens"]
                - usage["cache_write_input_tokens"]
            )
            require(
                uncached >= 0 and usage["reasoning_output_tokens"] <= usage["output_tokens"],
                "Contradictory usage",
            )
            inp, cached, write, out = PRICES[model]
            calculated = (
                uncached * inp
                + usage["cached_input_tokens"] * cached
                + usage["cache_write_input_tokens"] * write
                + usage["output_tokens"] * out
            ) / 1e6
            require(math.isclose(calculated, c["cost_usd"], abs_tol=1e-12), "Candidate cost")
            require(
                math.isfinite(c["latency_seconds"]) and c["latency_seconds"] >= 0, "Invalid latency"
            )
            calls += 1
            cost += calculated
        require(calls == report["calls"] == run["calls"], "Run call count")
        require(math.isclose(cost, report["cost_usd"], abs_tol=1e-12), "Report cost")
        require(math.isclose(cost, run["cost_usd"], abs_tol=1e-12), "Manifest run cost")
    for model in MODELS:
        require(set(results[model]) == set(lookup), "Incomplete candidate coverage")
    pair_fields = ("candidate", "candidate_id", "context", "suppression_reason", "prompt_sha256")
    for key in lookup:
        a, b = (results[model][key] for model in MODELS)
        require(all(a.get(f) == b.get(f) for f in pair_fields), "Paired input mismatch")
        require(
            (a["status"] == "context_unavailable") == (b["status"] == "context_unavailable"),
            "Paired eligibility mismatch",
        )
    summaries = {}
    joined = []
    for model, items in results.items():
        called = [c for c in items.values() if "usage" in c]
        require(len(called) == 24, "Expected 24 calls per model")
        cross = {}
        full_labels = set()
        for key, c in items.items():
            assessment = lookup[key]["assessment"]
            cross.setdefault(assessment, Counter())[c["status"]] += 1
            if c["status"] == "confirmed" and lookup[key]["label_coverage"] == "full":
                full_labels.add(lookup[key]["approved_label_id"])
        latencies = sorted(c["latency_seconds"] for c in called)
        summaries[model] = {
            "candidates": len(items),
            "calls": len(called),
            "status_counts": dict(Counter(c["status"] for c in items.values())),
            "cost_usd": round(sum(c["cost_usd"] for c in called), 9),
            "median_latency_seconds": median(latencies),
            "p95_latency_seconds_nearest_rank": latencies[math.ceil(0.95 * len(latencies)) - 1],
            "usage": {t: sum(c["usage"][t] for c in called) for t in TOKEN_FIELDS},
            "blind_assessment_by_status": {k: dict(v) for k, v in sorted(cross.items())},
            "unique_full_approved_labels_confirmed": sorted(full_labels),
        }
    for key, label in lookup.items():
        joined.append(
            {
                "arm": key[0],
                "case_id": key[1],
                "origin": key[2],
                "original_index": key[3],
                "candidate_id": label["candidate_id"],
                "assessment": label["assessment"],
                "approved_label_id": label["approved_label_id"],
                "label_coverage": label["label_coverage"],
                "context_caveat": label["context_caveat"],
                "verdicts": {m: results[m][key]["status"] for m in MODELS},
                "prompt_sha256": results[MODELS[0]][key].get("prompt_sha256"),
            }
        )
    total = round(sum(v["cost_usd"] for v in summaries.values()), 9)
    require(
        sum(v["calls"] for v in summaries.values()) == ledger["requests_started"] == 48,
        "Ledger requests",
    )
    require(math.isclose(total, ledger["known_cost_usd"], abs_tol=1e-12), "Ledger cost")
    return {
        "schema_version": 1,
        "purpose": "Descriptive verifier smoke results, not accuracy",
        "manifest_sha256": sha(directory / "manifest.json"),
        "blind_audit_sha256": sha(directory / "blind-audit.json"),
        "blind_audit_status": audit["status"],
        "assessment_is_human_ground_truth": False,
        "coverage_scope": "Union across four generator arms; not one pipeline's recall",
        "paired_candidates": 27,
        "paired_prompts": 24,
        "total_calls": 48,
        "total_cost_usd": total,
        "approved_label_ids": sorted(all_labels),
        "models": summaries,
        "candidates": joined,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--write", action="store_true", help="Write generated comparison.json")
    args = parser.parse_args(argv)
    result = summarize(args.directory)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.write:
        # Replacing a link detaches the output, rather than overwriting its target.
        fd, temporary = tempfile.mkstemp(prefix="comparison-", suffix=".tmp", dir=args.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(rendered)
            os.replace(temporary, args.directory / "comparison.json")
        finally:
            Path(temporary).unlink(missing_ok=True)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
