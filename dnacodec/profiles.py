"""Situation profiles: what the storage channel looks like in a given situation.

Profiles live as JSON in profiles/. Agent A calibrates the numbers on real data.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"


@dataclass(frozen=True)
class SituationProfile:
    name: str
    description: str
    technology: str  # "nanopore" or "illumina"

    # Per-base error rates
    sub_rate: float
    ins_rate: float
    del_rate: float
    homopolymer_factor: float  # error multiplier per extra base in a run (1.0 = no effect)
    end_factor: float  # error multiplier at the last base vs the first, linear ramp (1.0 = uniform)

    # Strand loss and coverage
    dropout_rate: float  # fraction of strands lost before sequencing
    gc_dropout_factor: float  # extra dropout per 0.1 of GC deviation from 0.5
    coverage_mean: float  # mean reads per surviving strand (the reading budget)
    coverage_dispersion: float  # negative binomial shape; lower = more uneven coverage

    # Storage
    storage_years: float
    decay_per_year: float  # extra dropout fraction per year of storage

    # Costs. Placeholders until verified, never quote them in the pitch unverified.
    synthesis_usd_per_base: float
    sequencing_usd_per_read: float

    calibrated_from: str | None = None  # dataset the error numbers were fit to, None = guessed

    # Realism extensions. Defaults reproduce the simple model above.
    # Error multiplier by run length (index 0 = run of 1, last value applies to longer runs).
    # Overrides homopolymer_factor when set.
    homopolymer_run_factors: tuple[float, ...] | None = None
    read_quality_spread: float = 0.0  # sigma of a lognormal per-read error multiplier (mean 1)
    malformed_read_rate: float = 0.0  # fraction of reads that belong to another strand (clustering errors)

    def __post_init__(self) -> None:
        if self.technology not in ("nanopore", "illumina"):
            raise ValueError(f"unknown technology {self.technology!r}")
        for name in ("sub_rate", "ins_rate", "del_rate", "dropout_rate", "decay_per_year"):
            value = getattr(self, name)
            if not 0.0 <= value < 1.0:
                raise ValueError(f"{name}={value} must be in [0, 1)")
        if not 0.0 <= self.malformed_read_rate < 1.0 or self.read_quality_spread < 0:
            raise ValueError("malformed_read_rate must be in [0, 1) and read_quality_spread >= 0")
        if self.coverage_mean <= 0 or self.coverage_dispersion <= 0:
            raise ValueError("coverage_mean and coverage_dispersion must be positive")

    @property
    def total_dropout(self) -> float:
        """Base dropout plus storage decay, capped below 1."""
        return min(0.99, self.dropout_rate + self.decay_per_year * self.storage_years)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SituationProfile:
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown profile fields: {sorted(unknown)}")
        data = dict(data)
        if data.get("homopolymer_run_factors") is not None:
            data["homopolymer_run_factors"] = tuple(data["homopolymer_run_factors"])
        return cls(**data)


def load_profile(name_or_path: str | Path) -> SituationProfile:
    """Load by name (profiles/<name>.json) or by explicit path."""
    path = Path(name_or_path)
    if not path.suffix:
        path = PROFILES_DIR / f"{name_or_path}.json"
    return SituationProfile.from_dict(json.loads(path.read_text()))


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))


def save_profile(profile: SituationProfile, path: str | Path | None = None) -> Path:
    path = Path(path) if path else PROFILES_DIR / f"{profile.name}.json"
    path.write_text(json.dumps(profile.to_dict(), indent=2) + "\n")
    return path
