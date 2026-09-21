"""Retained paid evidence is immutable; all checks here are offline."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "eval/runs/2026-09-20-verifier-comparison"
SPEC = importlib.util.spec_from_file_location(
    "verifier_comparison_summary", ROOT / "eval/summarize_verifier_comparison.py"
)
assert SPEC is not None and SPEC.loader is not None
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def copy_artifacts(tmp_path):
    target = tmp_path / "experiment"
    shutil.copytree(ARTIFACTS, target)
    return target


def test_retained_summary_and_paired_evidence_reproduce():
    result = summary.summarize(ARTIFACTS)
    assert result == summary.read(ARTIFACTS / "comparison.json")
    assert result["paired_candidates"] == 27
    assert result["paired_prompts"] == 24
    assert result["total_calls"] == 48
    assert result["total_cost_usd"] == pytest.approx(0.704518)
    sol, terra = (result["models"][model] for model in summary.MODELS)
    assert sol["cost_usd"] == pytest.approx(0.469708)
    assert terra["cost_usd"] == pytest.approx(0.234810)
    assert sol["status_counts"] == {
        "confirmed": 16,
        "rejected": 4,
        "uncertain": 4,
        "context_unavailable": 3,
    }
    assert terra["status_counts"] == {
        "confirmed": 20,
        "rejected": 4,
        "context_unavailable": 3,
    }
    assert (
        sol["unique_full_approved_labels_confirmed"]
        == terra["unique_full_approved_labels_confirmed"]
    )
    assert len(sol["unique_full_approved_labels_confirmed"]) == 3
    assert len(result["approved_label_ids"]) == 5
    assert sol["blind_assessment_by_status"]["unsupported"] == {"rejected": 2}
    assert terra["blind_assessment_by_status"]["unsupported"] == {"confirmed": 1, "rejected": 1}


@pytest.mark.parametrize(
    "filename,error",
    [
        ("sources.json", "Source hash"),
        ("dataset-certified.jsonl", "Dataset hash"),
        ("raw/opus-5-low--sol-low/verification.json", "Report hash"),
    ],
)
def test_raw_byte_mutation_rejected(copy_artifacts, filename, error):
    path = copy_artifacts / filename
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match=error):
        summary.summarize(copy_artifacts)


def test_duplicate_run_rejected(copy_artifacts):
    path = copy_artifacts / "manifest.json"
    manifest = summary.read(path)
    manifest["runs"][1] = manifest["runs"][0]
    write(path, manifest)
    with pytest.raises(ValueError, match="Duplicate run"):
        summary.summarize(copy_artifacts)


def test_duplicate_audit_key_rejected(copy_artifacts):
    path = copy_artifacts / "blind-audit.json"
    audit = summary.read(path)
    audit["candidates"][1] = audit["candidates"][0]
    write(path, audit)
    with pytest.raises(ValueError, match="Duplicate/missing audit"):
        summary.summarize(copy_artifacts)


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("paired_prompt", "Paired input mismatch"),
        ("cost", "Candidate cost"),
        ("usage", "Contradictory usage"),
    ],
)
def test_semantic_mutation_with_rehashed_report_rejected(copy_artifacts, mutation, error):
    manifest_path = copy_artifacts / "manifest.json"
    manifest = summary.read(manifest_path)
    run = manifest["runs"][0]
    path = copy_artifacts / "raw" / run["name"] / "verification.json"
    report = summary.read(path)
    row = report["candidates"][0]
    if mutation == "paired_prompt":
        row["prompt_sha256"] = "0" * 64
    elif mutation == "cost":
        row["cost_usd"] += 0.01
    else:
        row["usage"]["cached_input_tokens"] = row["usage"]["input_tokens"] + 1
    write(path, report)
    run["report_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(manifest_path, manifest)
    with pytest.raises(ValueError, match=error):
        summary.summarize(copy_artifacts)


@pytest.mark.parametrize("missing", [True, False])
def test_identically_missing_prompt_hashes_cannot_pass_pairing(copy_artifacts, missing):
    manifest_path = copy_artifacts / "manifest.json"
    manifest = summary.read(manifest_path)
    for run in manifest["runs"][:2]:
        path = copy_artifacts / "raw" / run["name"] / "verification.json"
        report = summary.read(path)
        if missing:
            del report["candidates"][0]["prompt_sha256"]
        else:
            report["candidates"][0]["prompt_sha256"] = None
        write(path, report)
        run["report_sha256"] = summary.sha(path)
    write(manifest_path, manifest)
    with pytest.raises(ValueError, match="Missing/invalid prompt hash"):
        summary.summarize(copy_artifacts)


def test_write_detaches_hardlink_without_overwriting_raw_evidence(copy_artifacts):
    raw = copy_artifacts / "raw/opus-5-low--sol-low/verification.json"
    expected = raw.read_bytes()
    output = copy_artifacts / "comparison.json"
    output.unlink()
    output.hardlink_to(raw)
    summary.main([str(copy_artifacts), "--write"])
    assert raw.read_bytes() == expected
    assert summary.read(output) == summary.summarize(copy_artifacts)
    assert not list(copy_artifacts.glob("comparison-*.tmp"))
