"""Evaluate reply classification and shipment extraction against the labelled dataset.

Usage (`make eval`)::

    export LLM_PROVIDER=anthropic LLM_MODEL=claude-opus-5 ANTHROPIC_API_KEY=...
    uv run python evals/run_evals.py

Runs each example through the real tasks (rules first, then the configured LLM), writes
`evals/reports/<timestamp>.json` and `.md` (accuracy per bucket, confusion matrix, field-level
extraction accuracy) and exits non-zero when accuracy is below `EVAL_MIN_BUCKET_ACCURACY` or
`EVAL_MIN_EXTRACTION_ACCURACY`. With the mock provider it runs offline as a smoke test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from lanereach.ai.llm.factory import build_llm_client
from lanereach.ai.rules.reply_rules import ReplyMessage
from lanereach.ai.schemas import ShipmentDetails
from lanereach.ai.tasks.classify_reply import ReplyContext, classify_reply
from lanereach.ai.tasks.extract_shipment_details import extract_shipment_details
from lanereach.ai.tasks.runtime import AIRuntime
from lanereach.core.clock import SystemClock
from lanereach.core.config import Settings, get_settings

ROOT = Path(__file__).parent
DATASET = ROOT / "datasets" / "labelled_replies.jsonl"
REPORTS = ROOT / "reports"
SHIPMENT_FIELDS = (
    "origin_country",
    "destination_country",
    "mode",
    "container_type",
    "container_quantity",
    "incoterm",
    "frequency",
)


class InMemoryRunRecorder:
    """Records AI runs in memory (evals do not touch the database)."""

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    async def record_run(self, **fields: Any) -> uuid.UUID:
        """Remember the run."""
        self.runs.append(fields)
        return uuid.uuid4()


@dataclass
class ExampleResult:
    """Outcome of one example."""

    id: str
    language: str
    expected: str
    predicted: str
    source: str
    field_hits: dict[str, bool]
    error: str | None = None


def shipment_fields(details: ShipmentDetails) -> dict[str, Any]:
    """Flatten extracted details into the dataset's field names."""
    return {
        "origin_country": details.origin.country if details.origin else None,
        "destination_country": details.destination.country if details.destination else None,
        "mode": details.mode.value,
        "container_type": details.container_type.value if details.container_type else None,
        "container_quantity": details.container_quantity,
        "incoterm": details.incoterm.value if details.incoterm else None,
        "frequency": details.frequency.value,
    }


async def evaluate(settings: Settings, examples: list[dict[str, Any]]) -> list[ExampleResult]:
    """Run every example through classification (and extraction when labelled)."""
    runtime = AIRuntime(
        llm=build_llm_client(settings, SystemClock()),
        recorder=InMemoryRunRecorder(),  # type: ignore[arg-type]
        settings=settings,
    )
    results: list[ExampleResult] = []
    for example in examples:
        message = ReplyMessage(
            subject=example.get("subject", ""),
            body=example["text"],
            received_on=date(2026, 9, 28),
            from_email=example.get("from_email", "prospect@example.com"),
        )
        try:
            outcome = await classify_reply(runtime, message, ReplyContext())
            hits: dict[str, bool] = {}
            expected_shipment = example.get("expected_shipment")
            if expected_shipment:
                extracted = shipment_fields(
                    (await extract_shipment_details(runtime, message)).output
                )
                hits = {
                    key: extracted.get(key) == value for key, value in expected_shipment.items()
                }
            results.append(
                ExampleResult(
                    example["id"],
                    example["language"],
                    example["expected_bucket"],
                    outcome.classification.bucket.value,
                    outcome.source,
                    hits,
                )
            )
        except Exception as exc:  # noqa: BLE001 - a failed example is a result, not a crash
            results.append(
                ExampleResult(
                    example["id"],
                    example["language"],
                    example["expected_bucket"],
                    "error",
                    "error",
                    {},
                    f"{type(exc).__name__}: {exc}",
                )
            )
    return results


def summarise(results: list[ExampleResult], settings: Settings) -> dict[str, Any]:
    """Accuracy per bucket and language, confusion matrix, field accuracy, pass/fail."""
    correct = sum(result.expected == result.predicted for result in results)
    per_bucket: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})
    per_language: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    field_totals: Counter[str] = Counter()
    field_hits: Counter[str] = Counter()
    for result in results:
        for group, key in ((per_bucket, result.expected), (per_language, result.language)):
            group[key]["total"] += 1
            group[key]["correct"] += result.expected == result.predicted
        confusion[result.expected][result.predicted] += 1
        for field_name, hit in result.field_hits.items():
            field_totals[field_name] += 1
            field_hits[field_name] += hit
    bucket_accuracy = correct / len(results) if results else 0.0
    extraction_accuracy = (
        sum(field_hits.values()) / sum(field_totals.values()) if field_totals else 1.0
    )
    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model or "mock-1",
        "examples": len(results),
        "bucket_accuracy": round(bucket_accuracy, 4),
        "extraction_accuracy": round(extraction_accuracy, 4),
        "thresholds": {
            "bucket_accuracy": settings.eval_min_bucket_accuracy,
            "extraction_accuracy": settings.eval_min_extraction_accuracy,
        },
        "passed": bucket_accuracy >= settings.eval_min_bucket_accuracy
        and extraction_accuracy >= settings.eval_min_extraction_accuracy,
        "per_bucket": dict(per_bucket),
        "per_language": dict(per_language),
        "confusion_matrix": {expected: dict(row) for expected, row in confusion.items()},
        "field_accuracy": {
            name: round(field_hits[name] / total, 4) for name, total in field_totals.items()
        },
        "rules_matched": sum(result.source == "rules" for result in results),
        "failures": [
            {
                "id": result.id,
                "expected": result.expected,
                "predicted": result.predicted,
                "error": result.error,
            }
            for result in results
            if result.expected != result.predicted
        ],
    }


def render_markdown(summary: dict[str, Any]) -> str:
    """Human-readable report."""
    lines = [
        f"# Eval report — {summary['provider']} / {summary['model']}",
        "",
        f"- Examples: {summary['examples']} (rules matched {summary['rules_matched']})",
        f"- Bucket accuracy: **{summary['bucket_accuracy']:.1%}** "
        f"(threshold {summary['thresholds']['bucket_accuracy']:.0%})",
        f"- Extraction field accuracy: **{summary['extraction_accuracy']:.1%}** "
        f"(threshold {summary['thresholds']['extraction_accuracy']:.0%})",
        f"- Result: {'PASS' if summary['passed'] else 'FAIL'}",
        "",
        "## Accuracy per bucket",
        "",
        "| Bucket | Correct | Total |",
        "|---|---|---|",
    ]
    lines += [
        f"| {bucket} | {row['correct']} | {row['total']} |"
        for bucket, row in sorted(summary["per_bucket"].items())
    ]
    lines += ["", "## Accuracy per language", "", "| Language | Correct | Total |", "|---|---|---|"]
    lines += [
        f"| {language} | {row['correct']} | {row['total']} |"
        for language, row in sorted(summary["per_language"].items())
    ]
    lines += ["", "## Extraction field accuracy", "", "| Field | Accuracy |", "|---|---|"]
    lines += [
        f"| {name} | {value:.0%} |" for name, value in sorted(summary["field_accuracy"].items())
    ]
    lines += ["", "## Confusion matrix (expected → predicted)", ""]
    for expected, row in sorted(summary["confusion_matrix"].items()):
        cells = ", ".join(f"{predicted}: {count}" for predicted, count in sorted(row.items()))
        lines.append(f"- **{expected}** → {cells}")
    if summary["failures"]:
        lines += ["", "## Misclassified", ""]
        lines += [
            f"- `{failure['id']}`: expected {failure['expected']}, got {failure['predicted']}"
            + (f" ({failure['error']})" if failure["error"] else "")
            for failure in summary["failures"]
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Run the evaluation and write reports.

    Returns:
        0 when thresholds are met, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path, default=REPORTS)
    arguments = parser.parse_args(argv)
    settings = get_settings()
    examples = [
        json.loads(line)
        for line in arguments.dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = summarise(asyncio.run(evaluate(settings, examples)), settings)
    arguments.out.mkdir(parents=True, exist_ok=True)
    stamp = f"{settings.llm_provider}-{uuid.uuid4().hex[:8]}"
    (arguments.out / f"{stamp}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (arguments.out / f"{stamp}.md").write_text(render_markdown(summary), encoding="utf-8")
    print(render_markdown(summary))
    print(f"Reports written to {arguments.out / stamp}.json/.md")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
