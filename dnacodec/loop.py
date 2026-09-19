"""The adaptive loop. Owner: Agent G.

For one situation profile:
  1. encode test data with current settings and scorer
  2. simulate the channel with train seeds
  3. fine-tune the decoder on this channel
  4. collect failures, retrain the risk model, adjust redundancy
  5. evaluate on held-out seeds, record an IterationResult
  6. stop when strand accuracy and density stop improving, or after max_iterations

Writes a RunResult via dnacodec.results.save_run after every iteration, so the dashboard
can show progress live and a crash never loses finished iterations.
"""

from __future__ import annotations

from .profiles import SituationProfile
from .results import RunResult
from .types import EncoderSettings


def run_loop(
    profile: SituationProfile,
    run_id: str,
    default_settings: EncoderSettings = EncoderSettings(),
    max_iterations: int = 5,
) -> RunResult:
    raise NotImplementedError("Agent G")
