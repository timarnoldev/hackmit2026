#!/usr/bin/env bash
# Downloads the public real datasets into data/raw/.
#   scripts/download_data.sh          Microsoft set + small DNAformer subset (~90 MB), for laptops
#   scripts/download_data.sh --full   everything (~1.2 GB), run this on the GPU machine
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw/dnaformer

if [ ! -d data/raw/microsoft ]; then
  git clone --depth 1 https://github.com/microsoft/clustered-nanopore-reads-dataset.git data/raw/microsoft
fi

BASE=https://zenodo.org/api/records/17473983/files
SMALL=(sequences_random_file.txt BinnedNanoporeSecondFlowcell_Random.txt)
FULL=(
  "${SMALL[@]}"
  sequences_semantic_file.txt rand_file.bin semantic_file.zip
  BinnedNanoporeFirstFlowcell_Random.txt BinnedNanoporeFirstFlowcell_Semantic.txt
  BinnedNanoporeSecondFlowcell_Semantic.txt
  BinnedNanoporeTwoFlowcells_Random.txt BinnedNanoporeTwoFlowcells_Semantic.txt
  BinnedTestIllumina_Random.txt BinnedTestIllumina_Semantic.txt
)
FILES=("${SMALL[@]}")
[ "${1:-}" = "--full" ] && FILES=("${FULL[@]}")

for f in "${FILES[@]}"; do
  echo "downloading $f"
  curl -fL -C - --retry 5 -o "data/raw/dnaformer/$f" "$BASE/$f/content"
done
echo "done"
