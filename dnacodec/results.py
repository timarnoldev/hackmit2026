"""Result files: the only contract between the loop, the experiments, and the dashboard.

results/<run_id>/<situation>.json   one RunResult per situation (the loop, the Pareto plot)
results/<run_id>/summary.json       one ExperimentSummary (rule audit, ablation, crossover, firewall, examples)

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
    """One alternation of the loop, evaluated on held-out seeds. One point on the Pareto plot."""

    iteration: int
    settings: EncoderSettings
    metrics: Metrics  # at the situation's read budget; metrics.bits_per_base is the Pareto x value
    min_reads_at_target: float | None = None  # Pareto y value: fewest mean reads per strand meeting the recovery target
    risky_kmers: list[tuple[str, float]] = field(default_factory=list)  # top patterns avoided, with learned risk
    notes: str = ""
    stage: str = ""  # "tier1" (system C: audited rules, rule scorer) or "alternation 0", "alternation 1", ...
    default_min_reads_matched: float | None = None  # default rules at this iteration's bits per base (matched density)


@dataclass
class CoveragePoint:
    decoder: str  # fixed names: "baseline" (system A), "transformer" (system B, default codec), "tailored" (final codec)
    coverage: float  # reads per strand
    strand_accuracy: float
    recovery_rate: float | None = None


@dataclass
class RunResult:
    situation: str
    profile: SituationProfile
    recovery_target: float  # e.g. 1.0 = all trials recovered
    n_trials: int  # held-out file trials per evaluation
    default_settings: EncoderSettings  # the one-size-fits-all codec (system B: same decoder)
    default_metrics: Metrics
    iterations: list[IterationResult]
    default_min_reads_at_target: float | None = None
    coverage_curve: list[CoveragePoint] = field(default_factory=list)
    is_mock: bool = False  # mock data must never reach the pitch
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def best(self) -> IterationResult:
        return self.iterations[-1]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> RunResult:
        return cls(
            situation=d["situation"],
            profile=SituationProfile.from_dict(d["profile"]),
            recovery_target=d["recovery_target"],
            n_trials=d["n_trials"],
            default_settings=EncoderSettings(**d["default_settings"]),
            default_metrics=Metrics(**d["default_metrics"]),
            iterations=[
                IterationResult(
                    iteration=it["iteration"],
                    settings=EncoderSettings(**it["settings"]),
                    metrics=Metrics(**it["metrics"]),
                    min_reads_at_target=it["min_reads_at_target"],
                    risky_kmers=[(k, r) for k, r in it["risky_kmers"]],
                    notes=it["notes"],
                    stage=it.get("stage", ""),
                    default_min_reads_matched=it.get("default_min_reads_matched"),
                )
                for it in d["iterations"]
            ],
            default_min_reads_at_target=d["default_min_reads_at_target"],
            coverage_curve=[CoveragePoint(**p) for p in d["coverage_curve"]],
            is_mock=d["is_mock"],
            created_at=d["created_at"],
        )


@dataclass
class AblationEntry:
    """Ablation ladder, same channel and held-out seeds:
    A fixed rules and redundancy + baseline decoder
    B fixed rules and redundancy + adapted transformer (the default we compare against)
    C audited rules + tuned redundancy, rule scorer only, B's frozen decoder (tier 1)
    D C + learned risk scorer, same frozen decoder (tier 2)
    E full alternating loop with re-adapted decoder"""

    situation: str
    system: str  # "A", "B", "C", "D" or "E"
    metrics: Metrics  # held-out, at the situation's read budget
    min_reads_at_target: float | None = None


@dataclass
class RuleAuditEntry:
    """Does one coding rule pay off on one channel? Measured from the default codec with the same
    decoder, toggling only this rule (or, for redundancy, comparing default vs tuned value)."""

    situation: str
    rule: str  # e.g. "max_homopolymer=3", "gc 0.4-0.6", "redundancy 0.3 vs tuned"
    min_reads_on: float | None  # reads per strand needed at the recovery target with the rule
    min_reads_off: float | None  # same without the rule (or with the tuned value)
    bits_per_base_on: float | None
    bits_per_base_off: float | None
    verdict: str  # "pays off", "no measurable benefit", "harmful"
    note: str = ""


@dataclass
class CrossoverEntry:
    """Codec tailored for one situation, evaluated on another situation's channel."""

    codec: str  # "default" or the situation the codec was tailored for
    channel: str  # situation whose channel it's evaluated on
    metrics: Metrics
    min_reads_at_target: float | None = None


@dataclass
class FirewallEntry:
    """Sim-to-real firewall. test is "sim_a_heldout", "sim_b" or "real"."""

    situation: str
    test: str
    metric: str  # e.g. "recovery_rate_gain", "min_reads_default", "min_reads_tailored", "risk_auc"
    value: float
    note: str = ""


@dataclass
class CandidateExample:
    """The same strand judged by each situation's risk model: accepted on one channel, rejected on another."""

    strand: str
    risk: dict[str, float]  # situation -> learned risk
    accepted: dict[str, bool]  # situation -> accepted by that situation's encoder


@dataclass
class ExperimentSummary:
    ablation: list[AblationEntry] = field(default_factory=list)
    rule_audit: list[RuleAuditEntry] = field(default_factory=list)
    crossover: list[CrossoverEntry] = field(default_factory=list)
    firewall: list[FirewallEntry] = field(default_factory=list)
    examples: list[CandidateExample] = field(default_factory=list)
    is_mock: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ExperimentSummary:
        return cls(
            ablation=[AblationEntry(**{**e, "metrics": Metrics(**e["metrics"])}) for e in d["ablation"]],
            rule_audit=[RuleAuditEntry(**e) for e in d.get("rule_audit", [])],
            crossover=[CrossoverEntry(**{**e, "metrics": Metrics(**e["metrics"])}) for e in d["crossover"]],
            firewall=[FirewallEntry(**e) for e in d["firewall"]],
            examples=[CandidateExample(**e) for e in d["examples"]],
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
        if p.name != "summary.json"
    ]


def save_summary(summary: ExperimentSummary, run_id: str) -> Path:
    path = RESULTS_DIR / run_id / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary.to_dict(), indent=2) + "\n")
    return path


def load_summary(run_id: str) -> ExperimentSummary | None:
    path = RESULTS_DIR / run_id / "summary.json"
    return ExperimentSummary.from_dict(json.loads(path.read_text())) if path.exists() else None
