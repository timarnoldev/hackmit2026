/* Erbgut deck: THE ONE PLACE FOR RESULTS.
 *
 * Every number the deck shows about the final runs comes from this object. Anything left null
 * renders as an amber dashed "[RESULT: ...]" placeholder, so a missing number can never be
 * mistaken for a real one. Only fill values that came out of dnacodec.evaluate on held-out
 * seeds and that the ML verifier signed off.
 *
 * Fill it by hand, or generate it from a finished run:
 *     python3 marketing/deck/tools/results_from_run.py <run_id>
 * (reads results/<run_id>/summary.json and the per-channel run files, keeps meta, headline, qa
 * and crossover.summary from this file, and rewrites the rest).
 *
 * Keep the part after "window.RESULTS =" valid JSON: double quotes, no trailing commas, no
 * comments inside the object. If this file has a syntax error the deck still opens, shows a
 * red banner, and treats every result as pending.
 *
 * Fields
 *   meta.team            "Name (role), Name (role)" shown on the title and closing slides
 *   meta.source          where the numbers came from, e.g. "results/final-0920"
 *   meta.sample          true only in results.sample.js (shows the SAMPLE DATA badge)
 *
 *   ruleAudit[]          one row per rule (tier 1 slide). Per channel ("nanopore", "illumina"):
 *     verdict            "pays off" | "no measurable benefit" | "harmful"      (hard rules)
 *                        "tuned is better" | "default is fine"                 (redundancy row)
 *     readsOn, readsOff  reads per strand needed at the recovery target with / without the rule
 *                        (redundancy row: default 0.3 / tuned value). null = not reached.
 *     tunedRedundancy    redundancy row only, e.g. 0.2
 *     bpbOn, bpbOff      optional bits per base for the two settings
 *     The first row must stay the run-length rule: the speaker notes quote it.
 *
 *   pareto.channel       which channel the Pareto slide shows ("nanopore")
 *   pareto.<channel>.default         { bpb, reads } the default codec (system B)
 *   pareto.<channel>.rounds[]        { label, bpb, reads } one per tuning round, in order;
 *                                    the last one is "the tuned codec"
 *   pareto.<channel>.matchedDefault  { bpb, reads } default rules at the tuned codec's bits per
 *                                    base. The results slide compares this with the last round.
 *
 *   ablation             reads per strand needed at the target for systems A to E, one channel
 *   crossover.<codec>.<channel>      reads per strand needed; codec is "default", "nanopore"
 *                                    (tuned for Nanopore) or "illumina"
 *   crossover.summary    optional one-line outcome; otherwise computed if the pattern is
 *                        "wins at home, loses its edge away", else shown as a placeholder
 *   firewall.<test>      { status: "holds" | "does not hold" | null, note: short text }
 *                        tests: simAHeldout, simB, real
 *   headline.imageDemo   one sentence about the image round trip at 6 reads per strand
 *   qa.*                 short sentences for the appendix answers
 */
window.RESULTS = {
  "meta": {
    "sample": false,
    "source": null,
    "team": null
  },
  "ruleAudit": [
    {
      "id": "max_homopolymer=3",
      "label": "No run longer than 3",
      "detail": "max_homopolymer = 3",
      "nanopore": { "verdict": null, "readsOn": null, "readsOff": null },
      "illumina": { "verdict": null, "readsOn": null, "readsOff": null }
    },
    {
      "id": "gc 0.4-0.6",
      "label": "GC 40 to 60%",
      "detail": "gc_min 0.4, gc_max 0.6",
      "nanopore": { "verdict": null, "readsOn": null, "readsOff": null },
      "illumina": { "verdict": null, "readsOn": null, "readsOff": null }
    },
    {
      "id": "redundancy 0.3 vs tuned",
      "label": "30% extra strands",
      "detail": "redundancy 0.3 vs tuned",
      "nanopore": { "verdict": null, "readsOn": null, "readsOff": null, "tunedRedundancy": null, "bpbOn": null, "bpbOff": null },
      "illumina": { "verdict": null, "readsOn": null, "readsOff": null, "tunedRedundancy": null, "bpbOn": null, "bpbOff": null }
    }
  ],
  "pareto": {
    "channel": "nanopore",
    "nanopore": {
      "default": { "bpb": null, "reads": null },
      "rounds": [],
      "matchedDefault": { "bpb": null, "reads": null }
    },
    "illumina": {
      "default": { "bpb": null, "reads": null },
      "rounds": [],
      "matchedDefault": { "bpb": null, "reads": null }
    }
  },
  "ablation": { "channel": "nanopore", "A": null, "B": null, "C": null, "D": null, "E": null },
  "crossover": {
    "default": { "nanopore": null, "illumina": null },
    "nanopore": { "nanopore": null, "illumina": null },
    "illumina": { "nanopore": null, "illumina": null },
    "summary": null
  },
  "firewall": {
    "simAHeldout": { "status": null, "note": null },
    "simB": { "status": null, "note": null },
    "real": { "status": null, "note": null }
  },
  "headline": {
    "imageDemo": null
  },
  "qa": {
    "riskModelRealAuc": null,
    "riskTopPatterns": null,
    "handRulesOptimalOn": null
  }
};
