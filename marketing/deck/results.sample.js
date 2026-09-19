/* SAMPLE DATA. NOT RESULTS. Every number in this file is made up.
 *
 * It exists only so the charts and animations can be previewed before the real runs finish:
 * open the deck with ?sample (for example http://localhost:8000/?sample). The deck then shows a
 * SAMPLE DATA badge on every slide and in the presenter view. Never present with this file,
 * never copy its numbers into results.js. Same format as results.js.
 */
window.RESULTS = {
  "meta": {
    "sample": true,
    "source": "SAMPLE, made up for previewing",
    "team": "SAMPLE Team Name (role), Name (role)"
  },
  "ruleAudit": [
    {
      "id": "max_homopolymer=3",
      "label": "No run longer than 3",
      "detail": "max_homopolymer = 3",
      "nanopore": { "verdict": "pays off", "readsOn": 10, "readsOff": 14 },
      "illumina": { "verdict": "no measurable benefit", "readsOn": 6, "readsOff": 6 }
    },
    {
      "id": "gc 0.4-0.6",
      "label": "GC 40 to 60%",
      "detail": "gc_min 0.4, gc_max 0.6",
      "nanopore": { "verdict": "no measurable benefit", "readsOn": 10, "readsOff": 10 },
      "illumina": { "verdict": "harmful", "readsOn": 6, "readsOff": 5 }
    },
    {
      "id": "redundancy 0.3 vs tuned",
      "label": "30% extra strands",
      "detail": "redundancy 0.3 vs tuned",
      "nanopore": { "verdict": "default is fine", "readsOn": 10, "readsOff": 10, "tunedRedundancy": 0.3, "bpbOn": 1.21, "bpbOff": 1.21 },
      "illumina": { "verdict": "tuned is better", "readsOn": 6, "readsOff": 6, "tunedRedundancy": 0.15, "bpbOn": 1.21, "bpbOff": 1.37 }
    }
  ],
  "pareto": {
    "channel": "nanopore",
    "nanopore": {
      "default": { "bpb": 1.21, "reads": 10 },
      "rounds": [
        { "label": "tier 1", "bpb": 1.26, "reads": 9 },
        { "label": "round 1", "bpb": 1.28, "reads": 8 },
        { "label": "round 2", "bpb": 1.28, "reads": 7 }
      ],
      "matchedDefault": { "bpb": 1.28, "reads": 11 }
    },
    "illumina": {
      "default": { "bpb": 1.21, "reads": 6 },
      "rounds": [
        { "label": "tier 1", "bpb": 1.37, "reads": 6 },
        { "label": "round 1", "bpb": 1.38, "reads": 5 }
      ],
      "matchedDefault": { "bpb": 1.38, "reads": 7 }
    }
  },
  "ablation": { "channel": "nanopore", "A": 14, "B": 10, "C": 9, "D": 7, "E": 7 },
  "crossover": {
    "default": { "nanopore": 10, "illumina": 6 },
    "nanopore": { "nanopore": 7, "illumina": 7 },
    "illumina": { "nanopore": 12, "illumina": 5 },
    "summary": null
  },
  "firewall": {
    "simAHeldout": { "status": "holds", "note": "SAMPLE" },
    "simB": { "status": "holds", "note": "SAMPLE, smaller gain" },
    "real": { "status": "does not hold", "note": "SAMPLE" }
  },
  "headline": {
    "imageDemo": "SAMPLE: the default codec fails to recover the image, the tuned codec gets it back bit for bit"
  },
  "qa": {
    "transformerVsBaseline": "SAMPLE: transformer vs baseline sentence goes here.",
    "riskModelRealAuc": "SAMPLE: AUC 0.xx on held-out real clusters.",
    "riskTopPatterns": "SAMPLE: top patterns go here.",
    "handRulesOptimalOn": "SAMPLE: channel list goes here."
  }
};
