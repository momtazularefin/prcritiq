"""Independent replay preflight and fail-closed execution checks; no live calls."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from prcritiq.benchmark import certify_dataset
from prcritiq.config import Settings
from prcritiq.dataset import BenchmarkCase, Label, read_dataset, write_dataset
from prcritiq.findings import CandidateFinding
from prcritiq.providers import ProviderBillingError, ProviderError, Usage
from prcritiq.verification import VerificationDecision, VerificationResult
from prcritiq.verification_replay import ReplayError, run_verification_replay

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
BASE_SOURCE = "def retry(n):\n    return 0 if n == 0 else 1 / n\n"
HEAD_SOURCE = "def retry(n):\n    return 1 / n\n"
PATCH = "@@ -1,2 +1,2 @@\n def retry(n):\n-    return 0 if n == 0 else 1 / n\n+    return 1 / n\n"
LABEL_MARKER = "SECRET_HUMAN_LABEL_913"
EXPECTED_MARKER = "SECRET_EXPECTED_ISSUE_247"
NOTES_MARKER = "SECRET_ADJUDICATION_531"


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate(**overrides) -> dict:
    values = {
        "file_path": "src/app.py",
        "line": 2,
        "severity": "high",
        "confidence": 95,
        "category": "bug",
        "finding": "The changed division fails for a zero argument.",
        "evidence": "The head return divides by n after the zero guard was removed.",
        "suggested_fix": "Restore the zero guard before division.",
        "source_refs": [],
    }
    values.update(overrides)
    return CandidateFinding(**values).model_dump(mode="json")


@pytest.fixture
def make_replay(tmp_path: Path):
    def make(*, count: int = 1):
        cases = []
        report_cases = []
        bundle_cases = []
        for number in range(1, count + 1):
            case_id = f"case-{number}"
            fixture_name = f"{case_id}.json"
            human_comment = f"{LABEL_MARKER}: Removing this guard raises on a zero argument."
            _write(
                tmp_path / fixture_name,
                {
                    "metadata": {
                        "repo": "example/repo",
                        "number": number,
                        "title": "Simplify retry",
                        "state": "closed",
                        "base_sha": BASE_SHA,
                        "head_sha": HEAD_SHA,
                        "author_login": "author",
                        "html_url": f"https://github.com/example/repo/pull/{number}",
                    },
                    "files": [
                        {
                            "filename": "src/app.py",
                            "status": "modified",
                            "additions": 1,
                            "deletions": 1,
                            "changes": 2,
                            "patch": PATCH,
                            "previous_filename": None,
                        }
                    ],
                    "review_comments": [
                        {
                            "id": number,
                            "path": "src/app.py",
                            "line": 2,
                            "original_line": 2,
                            "body": human_comment,
                            "user": "reviewer",
                            "user_type": "User",
                            "commit_id": HEAD_SHA,
                            "in_reply_to_id": None,
                        }
                    ],
                },
            )
            cases.append(
                BenchmarkCase(
                    id=case_id,
                    repo="example/repo",
                    pr_number=number,
                    base_sha=BASE_SHA,
                    head_sha=HEAD_SHA,
                    languages=["python"],
                    diff_path=fixture_name,
                    human_comments_path=fixture_name,
                    labels=[
                        Label(
                            file_path="src/app.py",
                            line=2,
                            category="bug",
                            severity="high",
                            human_comment=human_comment,
                            expected_issue=EXPECTED_MARKER,
                            label_id=f"github-review-comment:{number}",
                            reviewer_login="reviewer",
                            source_comment_id=number,
                            review_commit_sha=HEAD_SHA,
                            adjudication="confirmed_defect",
                            adjudication_notes=NOTES_MARKER,
                            human_approved=True,
                            adjudicator="test-human",
                        )
                    ],
                )
            )
            report_cases.append(
                {
                    "case_id": case_id,
                    "repo": "example/repo",
                    "pr_number": number,
                    "published_findings": [
                        {
                            "candidate": _candidate(),
                            "publish_decision": "publish",
                            "suppression_reason": None,
                        }
                    ],
                    "suppressed_findings": [],
                }
            )
            bundle_cases.append(
                {
                    "case_id": case_id,
                    "repo": "example/repo",
                    "base_sha": BASE_SHA,
                    "head_sha": HEAD_SHA,
                    "base": {"src/app.py": BASE_SOURCE},
                    "head": {"src/app.py": HEAD_SOURCE},
                }
            )
        paths = {
            "root": tmp_path,
            "dataset_path": tmp_path / "dataset.jsonl",
            "report_path": tmp_path / "original.json",
            "source_bundle_path": tmp_path / "sources.json",
            "out_dir": tmp_path / "replay-output",
        }
        write_dataset(cases, paths["dataset_path"])
        _write(
            paths["report_path"],
            {
                "dataset_version": "dataset.jsonl",
                "mode": "model",
                "model_policy": "openai",
                "model": "gpt-5.6-terra",
                "cases": report_cases,
            },
        )
        _write(paths["source_bundle_path"], {"schema_version": 1, "cases": bundle_cases})
        assert certify_dataset(read_dataset(paths["dataset_path"]), tmp_path).certified
        return paths

    return make


class RecordingVerifier:
    def __init__(
        self, *, verdict="confirmed", error=None, invented_refs=False, decision_overrides=None
    ):
        self.calls = []
        self.verdict = verdict
        self.error = error
        self.invented_refs = invented_refs
        self.decision_overrides = decision_overrides or {}
        self.usage = Usage(input_tokens=100, output_tokens=25, cache_write_input_tokens=20)

    def verify(self, *, system, user, choice):
        self.calls.append((system, user, choice))
        if self.error is not None:
            raise self.error
        payload = json.loads(user)
        refs = [item["source_id"] for item in payload["context"]["snippets"]]
        positive = self.verdict in {"confirmed", "partial"}
        values = {
            "verdict": self.verdict,
            "rationale": "The supplied source supports this verdict about the allegation.",
            "failure_scenario": "retry(0) raises in head but returned zero in base."
            if positive
            else "",
            "counterevidence": (
                "The zero-input failure holds, but the allegation's wider scope is not established."
                if self.verdict == "partial"
                else "No head guard prevents the division."
            ),
            "source_refs": ["invented-source"] if self.invented_refs else refs,
            "support_basis": "introduced_failure" if positive else "not_established",
            "base_behavior": "retry(0) returns zero without dividing." if positive else "",
            "head_behavior": "retry(0) raises ZeroDivisionError." if positive else "",
            "contract_evidence": "",
            "contract_source_refs": [],
        }
        values.update(self.decision_overrides)
        return VerificationResult(
            decision=VerificationDecision(**values),
            usage=self.usage,
        )


class NetworkForbidden:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def forbidden(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            raise AssertionError("Network must not be used before static preflight completes")

        return forbidden


def _run(paths, **overrides):
    options = {**paths, "settings": Settings(), **overrides}
    return run_verification_replay(**options)


def test_entire_dataset_certified_before_selection_calls_or_network(make_replay) -> None:
    paths = make_replay(count=2)
    cases = read_dataset(paths["dataset_path"])
    bad_label = replace(cases[1].labels[0], human_approved=False)
    cases[1] = replace(cases[1], labels=[bad_label])
    write_dataset(cases, paths["dataset_path"])
    verifier = RecordingVerifier()
    client = NetworkForbidden()

    with pytest.raises(ReplayError):
        _run(
            paths,
            source_bundle_path=None,
            fetch_context=True,
            live=True,
            max_candidates=1,
            verifier=verifier,
            client=client,
        )

    assert verifier.calls == []
    assert client.calls == []


@pytest.mark.parametrize(
    "mutation",
    ["not_object", "missing_cases", "bad_cases", "unknown_case", "duplicate_case", "bad_candidate"],
)
def test_malformed_report_rejected_before_provider_or_network(make_replay, mutation) -> None:
    paths = make_replay()
    payload = _read(paths["report_path"])
    if mutation == "not_object":
        payload = []
    elif mutation == "missing_cases":
        del payload["cases"]
    elif mutation == "bad_cases":
        payload["cases"] = "not a list"
    elif mutation == "unknown_case":
        payload["cases"][0]["case_id"] = "not-in-dataset"
    elif mutation == "duplicate_case":
        payload["cases"].append(payload["cases"][0])
    else:
        del payload["cases"][0]["published_findings"][0]["candidate"]["finding"]
    _write(paths["report_path"], payload)
    verifier = RecordingVerifier()
    client = NetworkForbidden()

    with pytest.raises(ReplayError):
        _run(
            paths,
            source_bundle_path=None,
            fetch_context=True,
            live=True,
            verifier=verifier,
            client=client,
        )

    assert verifier.calls == []
    assert client.calls == []


@pytest.mark.parametrize(
    "mutation",
    ["not_object", "wrong_schema", "unknown_case", "duplicate_case", "wrong_sha", "bad_sources"],
)
def test_malformed_bundle_rejected_before_provider(make_replay, mutation) -> None:
    paths = make_replay()
    payload = _read(paths["source_bundle_path"])
    if mutation == "not_object":
        payload = []
    elif mutation == "wrong_schema":
        payload["schema_version"] = 2
    elif mutation == "unknown_case":
        payload["cases"][0]["case_id"] = "not-in-dataset"
    elif mutation == "duplicate_case":
        payload["cases"].append(payload["cases"][0])
    elif mutation == "wrong_sha":
        payload["cases"][0]["head_sha"] = "c" * 40
    else:
        payload["cases"][0]["head"] = ["src/app.py"]
    _write(paths["source_bundle_path"], payload)
    verifier = RecordingVerifier()

    with pytest.raises(ReplayError):
        _run(paths, live=True, verifier=verifier)

    assert verifier.calls == []


@pytest.mark.parametrize("side", ["base", "head"])
def test_source_must_match_frozen_patch_before_provider(make_replay, side) -> None:
    paths = make_replay()
    payload = _read(paths["source_bundle_path"])
    payload["cases"][0][side]["src/app.py"] = "def retry(n):\n    return 'wrong revision'\n"
    _write(paths["source_bundle_path"], payload)
    verifier = RecordingVerifier()

    with pytest.raises(ReplayError):
        _run(paths, live=True, verifier=verifier)

    assert verifier.calls == []


def test_dry_run_prepares_context_without_provider_and_preserves_inputs(make_replay) -> None:
    paths = make_replay()
    originals = {name: paths[name].read_bytes() for name in paths if name.endswith("_path")}
    verifier = RecordingVerifier()

    report = _run(paths, verifier=verifier)

    assert report["calls"] == 0
    assert report["schema_version"] == 2
    assert report["verification_protocol"] == "candidate-verifier-v2"
    assert report["cost_usd"] == 0
    assert report["aborted"] is None
    assert report["candidates"][0]["status"] == "prepared"
    assert report["candidates"][0]["candidate"] == _candidate()
    assert verifier.calls == []
    assert all(paths[name].read_bytes() == content for name, content in originals.items())


def test_missing_primary_source_is_context_unavailable_without_provider(make_replay) -> None:
    paths = make_replay()
    payload = _read(paths["source_bundle_path"])
    payload["cases"][0]["head"] = {}
    _write(paths["source_bundle_path"], payload)
    verifier = RecordingVerifier()

    report = _run(paths, live=True, verifier=verifier)

    assert report["candidates"][0]["status"] == "context_unavailable"
    assert report["calls"] == 0
    assert verifier.calls == []


@pytest.mark.parametrize("live", [False, True])
def test_complete_head_only_added_file_is_unavailable_before_any_paid_call(
    make_replay, live
) -> None:
    paths = make_replay()
    fixture_path = paths["root"] / "case-1.json"
    fixture = _read(fixture_path)
    fixture["files"][0].update(
        status="added",
        additions=2,
        deletions=0,
        changes=2,
        patch="@@ -0,0 +1,2 @@\n+def retry(n):\n+    return 1 / n\n",
    )
    _write(fixture_path, fixture)
    bundle = _read(paths["source_bundle_path"])
    bundle["cases"][0]["base"] = {}
    _write(paths["source_bundle_path"], bundle)
    verifier = RecordingVerifier()

    report = _run(paths, live=live, verifier=verifier)

    row = report["candidates"][0]
    assert row["status"] == "context_unavailable"
    assert row["context"]["snippets"]
    assert {snippet["side"] for snippet in row["context"]["snippets"]} == {"head"}
    assert "base_evidence_missing_for_v2" in row["context"]["issues"]
    assert row["context"]["truncated"] is False
    assert report["calls"] == 0
    assert report["cost_usd"] == 0
    assert verifier.calls == []
    assert "usage" not in row
    assert "prompt_sha256" not in row


def test_structurally_invalid_target_is_retained_but_not_verified(make_replay) -> None:
    paths = make_replay()
    payload = _read(paths["report_path"])
    payload["cases"][0]["published_findings"][0]["candidate"]["line"] = 999
    _write(paths["report_path"], payload)
    verifier = RecordingVerifier()

    report = _run(paths, live=True, verifier=verifier)

    assert report["candidates"][0]["status"] == "structural_reject"
    assert report["candidates"][0]["candidate"]["line"] == 999
    assert report["calls"] == 0
    assert verifier.calls == []


def test_low_confidence_suppressed_candidates_remain_eligible_and_unchanged(make_replay) -> None:
    paths = make_replay(count=2)
    payload = _read(paths["report_path"])
    second = payload["cases"][1]
    wrapper = second["published_findings"].pop()
    wrapper["candidate"]["confidence"] = 20
    wrapper["publish_decision"] = "suppress"
    wrapper["suppression_reason"] = "low_confidence"
    second["suppressed_findings"].append(wrapper)
    _write(paths["report_path"], payload)
    verifier = RecordingVerifier()

    report = _run(paths, live=True, verifier=verifier)

    assert report["calls"] == 2
    assert [row["status"] for row in report["candidates"]] == ["confirmed", "confirmed"]
    assert report["candidates"][1]["candidate"] == wrapper["candidate"]
    assert report["candidates"][1]["decision"]["verdict"] == "confirmed"


def test_call_cap_is_global_and_retains_unprocessed_candidates(make_replay) -> None:
    paths = make_replay(count=2)
    verifier = RecordingVerifier()

    report = _run(paths, live=True, max_candidates=1, verifier=verifier)

    assert report["calls"] == 1
    assert len(verifier.calls) == 1
    assert [row["status"] for row in report["candidates"]] == ["confirmed", "budget_exceeded"]


@pytest.mark.parametrize("fetch_context", [False, True])
def test_serialized_source_bundle_cap_fails_before_output_or_provider(
    make_replay, monkeypatch, fetch_context
) -> None:
    paths = make_replay()
    bundle = _read(paths["source_bundle_path"])
    # Compact input fits the cap; the reusable, indented output does not. The
    # unchanged extra source lies outside the patch and remains under file caps.
    for side in ("base", "head"):
        bundle["cases"][0][side]["src/app.py"] += "\n# " + "x" * 2_000 + "\n"
    _write(paths["source_bundle_path"], bundle)
    input_size = paths["source_bundle_path"].stat().st_size
    serialized_size = len(
        (json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    )
    assert paths["report_path"].stat().st_size < input_size < serialized_size
    monkeypatch.setattr("prcritiq.verification_replay._MAX_JSON_BYTES", input_size)
    fetch_calls = []

    def fake_fetch_sources(*, client, repo, ref, paths):
        fetch_calls.append((repo, ref, paths))
        side = "base" if ref == BASE_SHA else "head"
        return bundle["cases"][0][side]

    monkeypatch.setattr("prcritiq.verification_replay.fetch_sources", fake_fetch_sources)
    verifier = RecordingVerifier()
    client = NetworkForbidden()

    with pytest.raises(ReplayError, match="Prepared source bundle exceeds"):
        _run(
            paths,
            source_bundle_path=None if fetch_context else paths["source_bundle_path"],
            fetch_context=fetch_context,
            live=True,
            verifier=verifier,
            client=client,
        )

    assert len(fetch_calls) == (2 if fetch_context else 0)
    assert client.calls == []
    assert verifier.calls == []
    assert not paths["out_dir"].exists()


@pytest.mark.parametrize("oversized_text", ["x" * 32_000, "é" * 16_000], ids=["ascii", "utf8"])
def test_oversized_later_candidate_fails_before_any_fetch_or_provider(
    make_replay, oversized_text
) -> None:
    paths = make_replay(count=2)
    payload = _read(paths["report_path"])
    candidate = payload["cases"][1]["published_findings"][0]["candidate"]
    candidate["finding"] = oversized_text
    assert len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > 32_000
    _write(paths["report_path"], payload)
    verifier = RecordingVerifier()
    client = NetworkForbidden()

    with pytest.raises(ReplayError, match="Saved candidate exceeds the 32 KB"):
        _run(
            paths,
            source_bundle_path=None,
            fetch_context=True,
            live=True,
            max_candidates=1,
            verifier=verifier,
            client=client,
        )

    assert client.calls == []
    assert verifier.calls == []
    assert not paths["out_dir"].exists()


def test_candidate_at_exact_serialized_byte_limit_is_preserved(make_replay) -> None:
    paths = make_replay()
    payload = _read(paths["report_path"])
    candidate = payload["cases"][0]["published_findings"][0]["candidate"]
    candidate["finding"] = ""
    overhead = len(json.dumps(candidate, ensure_ascii=False).encode("utf-8"))
    candidate["finding"] = "x" * (32_000 - overhead)
    assert len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) == 32_000
    _write(paths["report_path"], payload)
    verifier = RecordingVerifier()

    report = _run(paths, verifier=verifier)

    assert report["candidates"][0]["candidate"] == candidate
    assert report["candidates"][0]["status"] == "prepared"
    assert verifier.calls == []


def test_billing_error_aborts_remaining_candidates(make_replay) -> None:
    paths = make_replay(count=2)
    verifier = RecordingVerifier(error=ProviderBillingError("no quota"))

    report = _run(paths, live=True, verifier=verifier)

    assert report["aborted"] == "billing_error"
    assert report["calls"] == 1
    assert len(verifier.calls) == 1
    assert [row["status"] for row in report["candidates"]] == ["provider_error", "aborted"]


def test_nonbilling_provider_error_also_aborts_to_avoid_repeated_failed_calls(make_replay) -> None:
    paths = make_replay(count=2)
    verifier = RecordingVerifier(error=ProviderError("temporary problem"))

    report = _run(paths, live=True, verifier=verifier)

    assert report["aborted"] == "provider_error"
    assert report["calls"] == 1
    assert [row["status"] for row in report["candidates"]] == ["provider_error", "aborted"]


def test_failed_verification_preserves_known_usage_without_persisting_error_body(
    make_replay,
) -> None:
    from prcritiq.verification import VerificationProviderError

    paths = make_replay(count=2)
    known_usage = Usage(
        input_tokens=100,
        output_tokens=25,
        cached_input_tokens=40,
        cache_write_input_tokens=20,
        reasoning_output_tokens=10,
    )
    verifier = RecordingVerifier(
        error=VerificationProviderError("SENSITIVE_REMOTE_BODY", usage=known_usage)
    )

    report = _run(paths, live=True, model="gpt-5.6-sol", verifier=verifier)

    assert report["aborted"] == "provider_error"
    assert report["calls"] == 1
    assert report["usage_complete"] is True
    assert report["cost_usd"] == pytest.approx(known_usage.cost_usd("gpt-5.6-sol"))
    assert report["candidates"][0]["usage"]["cache_write_input_tokens"] == 20
    assert report["candidates"][0]["cost_usd"] == report["cost_usd"]
    assert report["candidates"][1]["status"] == "aborted"
    assert "SENSITIVE_REMOTE_BODY" not in json.dumps(report)


def test_invalid_references_fail_closed_but_usage_is_still_charged(make_replay) -> None:
    paths = make_replay()
    verifier = RecordingVerifier(invented_refs=True)

    report = _run(paths, live=True, model="gpt-5.6-sol", verifier=verifier)

    assert report["candidates"][0]["status"] == "invalid_decision"
    assert report["candidates"][0]["decision"]["source_refs"] == ["invented-source"]
    assert report["calls"] == 1
    assert report["cost_usd"] == pytest.approx(verifier.usage.cost_usd("gpt-5.6-sol"))


@pytest.mark.parametrize("verdict", ["confirmed", "partial", "rejected", "uncertain"])
def test_decisions_keep_original_candidate_and_do_not_leak_labels(make_replay, verdict) -> None:
    paths = make_replay()
    verifier = RecordingVerifier(verdict=verdict)

    report = _run(paths, live=True, verifier=verifier)

    row = report["candidates"][0]
    assert row["status"] == verdict
    assert row["candidate"] == _candidate()
    assert row["decision"]["verdict"] == verdict
    assert report["schema_version"] == 2
    assert report["verification_protocol"] == "candidate-verifier-v2"
    assert report["status_counts"] == {verdict: 1}
    assert row["usage"] == asdict(verifier.usage)
    assert row["cost_usd"] == pytest.approx(verifier.usage.cost_usd("gpt-5.6-sol"))
    assert _read(paths["out_dir"] / "verification.json") == json.loads(json.dumps(report))
    system, user, choice = verifier.calls[0]
    assert choice.provider == "openai"
    assert choice.model == "gpt-5.6-sol"
    for marker in (LABEL_MARKER, EXPECTED_MARKER, NOTES_MARKER):
        assert marker not in system
        assert marker not in user
    assert "labels" not in json.loads(user)


@pytest.mark.parametrize(
    ("verdict", "overrides", "reason"),
    [
        ("confirmed", {"support_basis": "not_established"}, "missing_support_basis"),
        ("confirmed", {"base_behavior": ""}, "missing_behavior_comparison"),
        (
            "confirmed",
            {
                "base_behavior": "Looks up the empty key.",
                "head_behavior": "Looks up the empty key.",
            },
            "unchanged_behavior",
        ),
        ("confirmed", {"support_basis": "contract_violation"}, "missing_contract_evidence"),
        ("confirmed", {"contract_source_refs": ["invented-contract"]}, "unknown_source_ref"),
        ("partial", {"counterevidence": " \n"}, "missing_partial_qualification"),
    ],
)
def test_v2_invalid_decision_preserves_raw_decision_candidate_and_usage(
    make_replay, verdict, overrides, reason
) -> None:
    paths = make_replay()
    verifier = RecordingVerifier(verdict=verdict, decision_overrides=overrides)

    report = _run(paths, live=True, model="gpt-5.6-sol", verifier=verifier)

    row = report["candidates"][0]
    assert row["status"] == "invalid_decision"
    assert row["validation_error"] == reason
    assert row["candidate"] == _candidate()
    assert row["decision"]["verdict"] == verdict
    assert all(row["decision"][key] == value for key, value in overrides.items())
    assert row["usage"] == asdict(verifier.usage)
    assert report["calls"] == len(verifier.calls) == 1
    assert report["usage_complete"] is True
    assert report["aborted"] is None
    assert report["cost_usd"] == row["cost_usd"]
    assert report["cost_usd"] == pytest.approx(verifier.usage.cost_usd("gpt-5.6-sol"))
    assert report["status_counts"] == {"invalid_decision": 1}
    assert _read(paths["out_dir"] / "verification.json") == json.loads(json.dumps(report))


def test_partial_does_not_rewrite_or_promote_original_suppressed_candidate(make_replay) -> None:
    paths = make_replay()
    original = _read(paths["report_path"])
    wrapper = original["cases"][0]["published_findings"].pop()
    wrapper["candidate"]["confidence"] = 20
    wrapper["publish_decision"] = "suppress"
    wrapper["suppression_reason"] = "low_confidence"
    original["cases"][0]["suppressed_findings"].append(wrapper)
    _write(paths["report_path"], original)
    before = paths["report_path"].read_bytes()
    verifier = RecordingVerifier(verdict="partial")

    report = _run(paths, live=True, verifier=verifier)

    row = report["candidates"][0]
    assert row["status"] == "partial"
    assert row["origin"] == "suppressed"
    assert row["suppression_reason"] == "low_confidence"
    assert row["candidate"] == wrapper["candidate"]
    assert row["decision"]["counterevidence"]
    assert row["usage"] == asdict(verifier.usage)
    assert paths["report_path"].read_bytes() == before


def test_any_existing_output_directory_is_refused_without_overwriting(make_replay) -> None:
    paths = make_replay()
    paths["out_dir"].mkdir()
    verifier = RecordingVerifier()

    with pytest.raises(ReplayError):
        _run(paths, live=True, verifier=verifier)

    assert verifier.calls == []
    assert list(paths["out_dir"].iterdir()) == []


@pytest.mark.parametrize("both", [False, True])
def test_exactly_one_context_source_is_required(make_replay, both) -> None:
    paths = make_replay()
    verifier = RecordingVerifier()

    with pytest.raises(ReplayError):
        _run(
            paths,
            source_bundle_path=paths["source_bundle_path"] if both else None,
            fetch_context=both,
            live=True,
            verifier=verifier,
        )

    assert verifier.calls == []


def _cli_args(paths) -> list[str]:
    return [
        "verify",
        "--dataset",
        str(paths["dataset_path"]),
        "--report",
        str(paths["report_path"]),
        "--out",
        str(paths["out_dir"]),
        "--source-bundle",
        str(paths["source_bundle_path"]),
    ]


def test_cli_defaults_to_preparation_without_constructing_a_provider(
    make_replay, monkeypatch, capsys
) -> None:
    from prcritiq.cli import main

    paths = make_replay()
    monkeypatch.chdir(paths["root"])
    monkeypatch.setattr("prcritiq.cli.load_settings", lambda: Settings())

    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("Preparation must not construct a paid provider")

    monkeypatch.setattr("prcritiq.verification_replay.OpenAIVerifier", forbidden_provider)

    assert main(_cli_args(paths)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "prepare"
    assert output["calls"] == 0
    assert output["status_counts"] == {"prepared": 1}
    assert Path(output["report"]).is_file()


def test_cli_live_without_key_returns_preflight_error_and_no_output(
    make_replay, monkeypatch, capsys
):
    from prcritiq.cli import main

    paths = make_replay()
    monkeypatch.chdir(paths["root"])
    monkeypatch.setattr("prcritiq.cli.load_settings", lambda: Settings(openai_api_key=None))

    assert main([*_cli_args(paths), "--live"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert "OPENAI_API_KEY" in output["error"]
    assert not paths["out_dir"].exists()


def test_cli_provider_failure_returns_nonzero_and_checkpointed_result(
    make_replay, monkeypatch, capsys
) -> None:
    from prcritiq.cli import main

    paths = make_replay(count=2)
    monkeypatch.chdir(paths["root"])
    monkeypatch.setattr("prcritiq.cli.load_settings", lambda: Settings(openai_api_key="test-only"))
    verifier = RecordingVerifier(error=ProviderBillingError("no quota"))
    monkeypatch.setattr("prcritiq.verification_replay.OpenAIVerifier", lambda *_a, **_k: verifier)

    assert main([*_cli_args(paths), "--live"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["aborted"] == "billing_error"
    assert output["calls"] == 1
    assert output["usage_complete"] is False
    saved = _read(Path(output["report"]))
    assert [row["status"] for row in saved["candidates"]] == ["provider_error", "aborted"]


def test_cli_incomplete_preparation_returns_nonzero(make_replay, monkeypatch, capsys) -> None:
    from prcritiq.cli import main

    paths = make_replay()
    bundle = _read(paths["source_bundle_path"])
    bundle["cases"][0]["head"] = {}
    _write(paths["source_bundle_path"], bundle)
    monkeypatch.chdir(paths["root"])
    monkeypatch.setattr("prcritiq.cli.load_settings", lambda: Settings())

    assert main(_cli_args(paths)) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["calls"] == 0
    assert output["status_counts"] == {"context_unavailable": 1}
