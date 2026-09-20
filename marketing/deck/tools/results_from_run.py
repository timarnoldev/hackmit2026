#!/usr/bin/env python3
"""Fill marketing/deck/results.js from a finished run.

    python3 marketing/deck/tools/results_from_run.py <run_id> [--allow-mock] [--dry-run]

Reads results/<run_id>/summary.json and results/<run_id>/<situation>.json (the files written by
scripts/run_loop.py and scripts/run_experiments.py) and rewrites the data parts of results.js:
ruleAudit, pareto, ablation, crossover and firewall. It keeps what only a human can write:
meta (team, source), headline.imageDemo, qa.* and crossover.summary.

Standard library only, so it runs anywhere. Mock runs are refused unless --allow-mock, and then
the deck shows the SAMPLE DATA badge. Every number still goes past the ML verifier before the pitch.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

DECK = Path(__file__).resolve().parents[1]
REPO = DECK.parents[1]
RESULTS_JS = DECK / "results.js"
CHANNELS = {"nanopore_budget": "nanopore", "illumina_standard": "illumina"}
RULE_ROWS = [("max_homopolymer", 0), ("gc ", 1), ("redundancy", 2)]


def clean(v):
    """NaN and inf become null; everything else passes through."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def read_results_js(path: Path) -> tuple[str, dict]:
    text = path.read_text()
    marker = "window.RESULTS ="
    m = re.search(r"^window\.RESULTS\s*=", text, flags=re.M)
    if not m:
        sys.exit(f"{path} has no line starting with '{marker}'")
    head, rest = text[: m.start()], text[m.end():]
    body = rest[rest.index("{"): rest.rindex("}") + 1]
    try:
        return head, json.loads(body)
    except json.JSONDecodeError as e:
        sys.exit(f"{path} is not valid JSON after '{marker}': {e}. Fix it by hand first.")


def stage_label(stage: str, k: int) -> str:
    if stage == "tier1":
        return "tier 1"
    m = re.match(r"alternation (\d+)", stage or "")
    return f"round {int(m.group(1)) + 1}" if m else f"round {k + 1}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id", help="folder name under results/")
    ap.add_argument("--allow-mock", action="store_true", help="accept is_mock runs (marks the deck as SAMPLE)")
    ap.add_argument("--dry-run", action="store_true", help="print the new object instead of writing results.js")
    args = ap.parse_args()

    run_dir = REPO / "results" / args.run_id
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        sys.exit(f"missing {summary_path}")
    summary = json.loads(summary_path.read_text())
    runs = {}
    for sit in CHANNELS:
        p = run_dir / f"{sit}.json"
        if p.exists():
            runs[sit] = json.loads(p.read_text())

    is_mock = bool(summary.get("is_mock")) or any(r.get("is_mock") for r in runs.values())
    if is_mock and not args.allow_mock:
        sys.exit("this run is marked is_mock. Refusing. Use --allow-mock only to preview, never for the pitch.")

    head, R = read_results_js(RESULTS_JS)
    notes: list[str] = []

    # ---- rule audit (tier 1)
    for e in summary.get("rule_audit", []):
        ch = CHANNELS.get(e["situation"])
        row = next((idx for prefix, idx in RULE_ROWS if e["rule"].startswith(prefix)), None)
        if ch is None or row is None or row >= len(R["ruleAudit"]):
            continue  # e.g. the "tuned codec vs default at matched density" entries feed the Pareto slide instead
        cell = R["ruleAudit"][row].setdefault(ch, {})
        cell.update(
            verdict=e.get("verdict"),
            readsOn=clean(e.get("min_reads_on")),
            readsOff=clean(e.get("min_reads_off")),
        )
        if row == 2:
            cell["bpbOn"] = clean(e.get("bits_per_base_on"))
            cell["bpbOff"] = clean(e.get("bits_per_base_off"))
            m = re.search(r"tuned redundancy ([0-9.]+)", e.get("note", ""))
            if m:
                cell["tunedRedundancy"] = float(m.group(1))
            elif e["situation"] in runs:
                its = runs[e["situation"]]["iterations"]
                tier1 = next((it for it in its if it.get("stage") == "tier1"), its[0] if its else None)
                if tier1:
                    cell["tunedRedundancy"] = tier1["settings"]["redundancy"]
        R["ruleAudit"][row]["detail"] = e["rule"]

    # ---- Pareto (default, each round, default rules at the tuned codec's bits per base)
    for sit, run in runs.items():
        ch = CHANNELS[sit]
        its = run.get("iterations", [])
        P = {
            "default": {"bpb": clean(run["default_metrics"].get("bits_per_base")), "reads": clean(run.get("default_min_reads_at_target"))},
            "rounds": [
                {"label": stage_label(it.get("stage", ""), k), "bpb": clean(it["metrics"].get("bits_per_base")), "reads": clean(it.get("min_reads_at_target"))}
                for k, it in enumerate(its)
            ],
            "matchedDefault": {"bpb": None, "reads": None},
        }
        if its:
            last = its[-1]
            P["matchedDefault"] = {"bpb": clean(last["metrics"].get("bits_per_base")), "reads": clean(last.get("default_min_reads_matched"))}
            if P["matchedDefault"]["reads"] is None:
                notes.append(f"{ch}: default_min_reads_matched is missing, the matched-density marker stays pending")
        R["pareto"][ch] = P

    # ---- ablation ladder, one channel
    ab_ch = R.get("ablation", {}).get("channel", "nanopore")
    for e in summary.get("ablation", []):
        if CHANNELS.get(e["situation"]) == ab_ch and e["system"] in "ABCDE":
            R["ablation"][e["system"]] = clean(e.get("min_reads_at_target"))

    # ---- crossover matrix
    for e in summary.get("crossover", []):
        codec = "default" if e["codec"] == "default" else CHANNELS.get(e["codec"])
        chan = CHANNELS.get(e["channel"])
        if codec and chan:
            R["crossover"].setdefault(codec, {})[chan] = clean(e.get("min_reads_at_target"))

    # ---- firewall, for the channel on the Pareto slide
    fw_sit = next((s for s, c in CHANNELS.items() if c == R["pareto"].get("channel", "nanopore")), None)
    fw = {(e["test"], e["metric"]): e for e in summary.get("firewall", []) if e["situation"] == fw_sit}
    F = R["firewall"]

    def verdict(default_reads, tuned_reads, what):
        if default_reads is None or tuned_reads is None:
            return None, f"{what}: not reached within the coverage grid"
        status = "holds" if tuned_reads < default_reads else "does not hold"
        return status, f"{default_reads:g} vs {tuned_reads:g} reads per strand"

    run = runs.get(fw_sit)
    if run and ("sim_a_heldout", "min_reads_default_matched") in fw and run.get("iterations"):
        d = clean(fw[("sim_a_heldout", "min_reads_default_matched")]["value"])
        t = clean(run["iterations"][-1].get("min_reads_at_target"))
        F["simAHeldout"]["status"], F["simAHeldout"]["note"] = verdict(d, t, "Simulator A")
    if ("sim_b", "not_run") in fw:
        F["simB"] = {"status": None, "note": None}
        notes.append("Simulator B was not run: the firewall step stays pending")
    elif ("sim_b", "min_reads_default") in fw and ("sim_b", "min_reads_tailored") in fw:
        d = clean(fw[("sim_b", "min_reads_default")]["value"])
        t = clean(fw[("sim_b", "min_reads_tailored")]["value"])
        F["simB"]["status"], F["simB"]["note"] = verdict(d, t, "Simulator B")
    if ("real", "risk_auc") in fw:
        auc = clean(fw[("real", "risk_auc")]["value"])
        if auc is not None:
            F["real"] = {"status": "measured", "note": f"risk model AUC {auc:.2f} on held-out real clusters"}

    R["meta"]["source"] = f"results/{args.run_id}"
    R["meta"]["sample"] = bool(is_mock)

    out = head + "window.RESULTS = " + json.dumps(R, indent=2) + ";\n"
    if args.dry_run:
        print(out)
    else:
        RESULTS_JS.write_text(out)
        print(f"wrote {RESULTS_JS.relative_to(REPO)} from results/{args.run_id}" + (" (MOCK, shows SAMPLE DATA)" if is_mock else ""))

    pending = []

    def walk(o, path):
        if o is None:
            pending.append(path)
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk(R, "")
    for n in notes:
        print("note:", n)
    print(f"still pending ({len(pending)}), shown as [RESULT: ...] placeholders:")
    for p in pending:
        print("  ", p)
    print("Check every firewall status and verdict by hand, and get the ML verifier's sign-off.")


if __name__ == "__main__":
    main()
