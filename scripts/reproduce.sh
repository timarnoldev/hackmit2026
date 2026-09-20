#!/usr/bin/env bash
# Reproduce the headline numbers of this project from scratch.
#
#   scripts/reproduce.sh            decoder numbers on real held-out reads + the demo (a few minutes)
#   scripts/reproduce.sh --full     also the simulator calibration check (a few minutes more)
#
# Needs: uv, and the datasets (scripts/download_data.sh). The trained polisher is in the repo.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf "\n\033[1m== %s\033[0m\n" "$*"; }

if [ ! -d data/raw/microsoft ]; then
  say "Datasets missing, downloading the Microsoft set (about 30 MB)"
  scripts/download_data.sh
fi

say "1. The classic baseline decoder on real held-out Nanopore reads"
echo "   1,996 clusters the pipeline never trained, tuned or calibrated on."
uv run python scripts/eval_real.py

say "2. The learned polisher on the same clusters, same protocol"
if [ -f checkpoints/polish/polish.pt ]; then
  uv run --extra train python -m dnacodec.model.benchmark checkpoints/polish/polish.pt --polish || {
    echo "   (needs torch: uv sync --extra train)"; }
else
  echo "   checkpoint checkpoints/polish/polish.pt missing, skipping"
fi

say "3. The end-to-end demo: a file through the Nanopore channel at 6 reads per strand"
echo "   Default codec against the tuned codec, 5 seeds fixed in advance."
uv run python scripts/demo_roundtrip.py --profile nanopore_budget --reads 6 --run-id run2 || true

if [ "${1:-}" = "--full" ]; then
  say "4. Is the simulator as hard as reality? Baseline on real vs simulated reads"
  uv run python - <<'PY'
import dataclasses
import numpy as np
from dnacodec import realdata
from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.profiles import load_profile
from dnacodec.simulator import simulate
from dnacodec.seeds import heldout_seeds

held = [c for c in realdata.load_microsoft("heldout") if c.reads]
refs = [c.reference for c in held]
profile = dataclasses.replace(
    load_profile("nanopore_budget"), coverage_mean=40.0, coverage_dispersion=1000.0,
    dropout_rate=0.0, decay_per_year=0.0, gc_dropout_factor=0.0,
)
seeds = heldout_seeds(3)
sim = simulate(refs, profile, seeds[0])
decoder, rng = MajorityVoteDecoder(), np.random.default_rng(seeds[1])
print(f"{len(held)} held-out clusters, exact strands")
print("reads   real    simulated")
for k in (2, 4, 6, 10, 16):
    real_cl, sim_cl = [], []
    for c, s in zip(held, sim):
        n = min(k, len(c.reads), len(s))
        real_cl.append(list(rng.choice(c.reads, n, replace=False)))
        sim_cl.append(list(rng.choice(s, n, replace=False)) if n else [])
    r = np.mean([o == x for o, x in zip(decoder.decode(real_cl, 110), refs)])
    v = np.mean([o == x for o, x in zip(decoder.decode(sim_cl, 110), refs)])
    print(f"{k:5d} {r:7.1%} {v:11.1%}")
PY
fi

say "Done"
echo "Full result sets of the tuning runs are in results/, and the dashboard shows them:"
echo "  uv sync --extra dashboard && uv run streamlit run dashboard/app.py"
echo "Every number we quote, with its provenance: docs/NUMBERS.md"
