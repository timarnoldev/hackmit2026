"""Result files: the only contract between the loop and the dashboard.

The loop writes one RunResult per situation to results/<run_id>/<situation>.json.
The dashboard only reads these files and never imports training code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .profiles import SituationProfile
from .types import EncoderSettings, Metrics

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


@dataclass
class IterationResult:
    iteration: int
    settings: EncoderSettings
    metrics: Metrics  # measured on held-out seeds
    risky_kmers: list[tuple[str, float]] = field(default_factory=list)  # top patterns avoided, with risk
    notes: str = ""


@dataclass
class CoveragePoint:
    decoder: str  # e.g. "baseline", "transformer", "tailored"
    coverage: float  # reads per strand
    strand_accuracy: float


@dataclass
class RunResult:
    situation: str
    profile: SituationProfile
    default_settings: EncoderSettings  # the one-size-fits-all codec we compare against
    default_metrics: Metrics
    iterations: list[IterationResult]
    coverage_curve: list[CoveragePoint] = field(default_factory=list)
    is_mock: bool = False  # mock data must never reach the pitch
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def best(self) -> IterationResult:
        return max(self.iterations, key=lambda it: it.metrics.strand_accuracy)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> RunResult:
        return cls(
            situation=d["situation"],
            profile=SituationProfile.from_dict(d["profile"]),
            default_settings=EncoderSettings(**d["default_settings"]),
            default_metrics=Metrics(**d["default_metrics"]),
            iterations=[
                IterationResult(
                    iteration=it["iteration"],
                    settings=EncoderSettings(**it["settings"]),
                    metrics=Metrics(**it["metrics"]),
                    risky_kmers=[(k, r) for k, r in it["risky_kmers"]],
                    notes=it["notes"],
                )
                for it in d["iterations"]
            ],
            coverage_curve=[CoveragePoint(**p) for p in d["coverage_curve"]],
            is_mock=d["is_mock"],
            created_at=d["created_at"],
        )


def save_run(run: RunResult, run_id: str) -> Path:
    path = RESULTS_DIR / run_id / f"{run.situation}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run.to_dict(), indent=2) + "\n")
    return path


def load_runs(run_id: str) -> list[RunResult]:
    return [
        RunResult.from_dict(json.loads(p.read_text()))
        for p in sorted((RESULTS_DIR / run_id).glob("*.json"))
    ]
