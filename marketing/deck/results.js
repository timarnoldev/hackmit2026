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
 *   ruleCase             one real slot from the encoder's candidate search: the strands, whether
 *                        the hand rules keep them, and the trained risk model's score for each.
 *                        Computed by scripts/rule_vs_model_case.py, never typed in.
 *   tier2                the paired scorer experiment: reads[], rules[], learned[] as recovery
 *                        rates over 300 held-out trials, plus held[] and reproduced
 *   crossover.<codec>.<channel>      reads per strand needed; codec is "default", "nanopore"
 *                                    (tuned for Nanopore) or "illumina"
 *   crossover.summary    optional one-line outcome; otherwise computed if the pattern is
 *                        "wins at home, loses its edge away", else shown as a placeholder
 *   firewall.ab          the same tier 2 change measured on Simulator A and on Simulator B,
 *                        rows[] per decoder with { rules, learned, diff, ci } for each simulator
 *                        (docs/NUMBERS.md section 5)
 *   firewall.<test>      { status: "holds" | "does not hold" | null, note: short text }
 *                        tests: simAHeldout, simB, real
 *   headline.imageDemo   one sentence about the image round trip at 6 reads per strand
 *   qa.*                 short sentences for the appendix answers
 */
window.RESULTS = {
  "meta": {
    "sample": false,
    "source": "results/run2_strict",
    "team": null
  },
  "ruleAudit": [
    {
      "id": "max_homopolymer=3",
      "label": "No run longer than 3",
      "detail": "max_homopolymer=3",
      "nanopore": {
        "verdict": "pays off",
        "readsOn": 19.5,
        "readsOff": 24.5
      },
      "illumina": {
        "verdict": "no measurable benefit",
        "readsOn": 3.0,
        "readsOff": 2.5
      }
    },
    {
      "id": "gc 0.4-0.6",
      "label": "GC 40 to 60%",
      "detail": "gc 0.4-0.6",
      "nanopore": {
        "verdict": "no measurable benefit",
        "readsOn": 19.5,
        "readsOff": 20.0
      },
      "illumina": {
        "verdict": "pays off",
        "readsOn": 3.0,
        "readsOff": 4.0
      }
    },
    {
      "id": "redundancy 0.3 vs tuned",
      "label": "30% extra strands",
      "detail": "redundancy 0.3 vs tuned",
      "nanopore": {
        "verdict": "tuned is better",
        "readsOn": 19.5,
        "readsOff": 5.5,
        "tunedRedundancy": 1.6,
        "bpbOn": 1.2021424902780835,
        "bpbOff": 0.6010712451390418
      },
      "illumina": {
        "verdict": "tuned is better",
        "readsOn": 3.0,
        "readsOff": 5.5,
        "tunedRedundancy": 0.1,
        "bpbOn": 1.2021424902780835,
        "bpbOff": 1.419880405581073
      }
    }
  ],
  "pareto": {
    "channel": "nanopore",
    "nanopore": {
      "default": {
        "bpb": 1.2021424902780835,
        "reads": 19.5
      },
      "rounds": [
        {
          "label": "tier 1",
          "bpb": 0.6363706983609104,
          "reads": 5.5
        },
        {
          "label": "round 1",
          "bpb": 0.6363706983609104,
          "reads": 5.0
        },
        {
          "label": "round 2",
          "bpb": 0.7192905435068926,
          "reads": 5.5
        },
        {
          "label": "round 3",
          "bpb": 0.6794956867949569,
          "reads": 5.0
        },
        {
          "label": "round 5",
          "bpb": 0.7192905435068926,
          "reads": 5.5
        }
      ],
      "matchedDefault": {
        "bpb": 0.7192905435068926,
        "reads": 6.0
      }
    },
    "illumina": {
      "default": {
        "bpb": 1.2021424902780835,
        "reads": 3.0
      },
      "rounds": [
        {
          "label": "tier 1",
          "bpb": 1.5042232831435915,
          "reads": 5.5
        },
        {
          "label": "round 1",
          "bpb": 1.5042232831435915,
          "reads": 5.5
        },
        {
          "label": "round 2",
          "bpb": 1.5042232831435915,
          "reads": 5.0
        },
        {
          "label": "round 3",
          "bpb": 1.5042232831435915,
          "reads": 5.5
        },
        {
          "label": "round 5",
          "bpb": 1.5042232831435915,
          "reads": 5.0
        }
      ],
      "matchedDefault": {
        "bpb": 1.5042232831435915,
        "reads": 5.5
      }
    }
  },
  "ruleCase": {
    "from": 17,
    "to": 56,
    "length": 110,
    "mean": 0.506,
    "oursN": 6,
    "candidates": [
      {
        "n": 1,
        "seq": "TGTAAGTAGTGTGGTGTGCAGGCACAAGTTGAATTAGATG",
        "risk": 0.62,
        "passes": true,
        "why": "",
        "rulesPick": true,
        "modelPick": false
      },
      {
        "n": 2,
        "seq": "AAGGGGTGTTAGACCGGGAGAATCTCAGGGAGGTGGGTTA",
        "risk": 0.63,
        "passes": false,
        "why": "run of 4",
        "rulesPick": false,
        "modelPick": false
      },
      {
        "n": 3,
        "seq": "CATGCGTTAGGCCTAAGGATGGGGGACTTTTTACTGGAAG",
        "risk": 0.594,
        "passes": false,
        "why": "run of 5",
        "rulesPick": false,
        "modelPick": false
      },
      {
        "n": 4,
        "seq": "GTTACGATATGATGTTTTATGCGTTTCGTTAGGTCTTCCG",
        "risk": 0.45,
        "passes": false,
        "why": "run of 4",
        "rulesPick": false,
        "modelPick": true
      },
      {
        "n": 5,
        "seq": "ACCATTACTGTAGACGCTGCCGCGTACTCGTCCCCGGCCG",
        "risk": 0.474,
        "passes": false,
        "why": "run of 4",
        "rulesPick": false,
        "modelPick": false
      },
      {
        "n": 6,
        "seq": "TAAATATATTCCTAGGGAAGAAACCTACTAGATTCGCCAT",
        "risk": 0.518,
        "passes": true,
        "why": "",
        "rulesPick": false,
        "modelPick": false
      }
    ]
  },
  "tier2": {
    "channel": "nanopore",
    "trials": 300,
    "reads": [4.0, 4.5, 5.0, 5.5, 6.0],
    "rules": [0.0, 0.27, 1.0, 1.0, 1.0],
    "learned": [0.1, 0.983, 0.997, 1.0, 1.0],
    "held": ["same redundancy", "same strand length", "same hard rules", "32 candidates", "same decoder", "same seeds", "same file"],
    "reproduced": "Repeated with the learned decoder on both simulators: strand failures fall 2.8 points"
  },
  "ablation": {
    "channel": "nanopore",
    "decoder": "baseline",
    "A": 19.5,
    "B": 19.5,
    "C": 5.5,
    "D": 5.0,
    "E": 5.5
  },
  "crossover": {
    "default": {
      "nanopore": 19.5,
      "illumina": 3.0
    },
    "nanopore": {
      "nanopore": 5.5,
      "illumina": 1.5
    },
    "illumina": {
      "nanopore": "not reached",
      "illumina": 5.0
    },
    "summary": "Each tuned codec wins at home and loses away, and the Illumina codec never recovers the file on Nanopore at all."
  },
  "firewall": {
    "ab": {
      "metric": "Strand failures at six reads per strand, Nanopore, 32 candidates per slot",
      "rows": [
        {
          "decoder": "Classic majority vote",
          "a": {
            "rules": 51.96,
            "learned": 48.01,
            "diff": -3.95,
            "ci": "-4.18 to -3.72"
          },
          "b": {
            "rules": 50.92,
            "learned": 46.76,
            "diff": -4.16,
            "ci": "-4.37 to -3.96"
          }
        },
        {
          "decoder": "With the learned polisher",
          "a": {
            "rules": 44.64,
            "learned": 41.88,
            "diff": -2.76,
            "ci": "-2.97 to -2.55"
          },
          "b": {
            "rules": 44.78,
            "learned": 41.93,
            "diff": -2.85,
            "ci": "-3.02 to -2.68"
          }
        }
      ]
    },
    "simAHeldout": {
      "status": "holds",
      "note": "6 vs 5.5 reads per strand"
    },
    "simB": {
      "status": "holds",
      "note": "24.5 vs 6 reads per strand"
    },
    "real": {
      "status": "measured",
      "note": "risk model AUC 0.52 on held-out real clusters"
    }
  },
  "headline": {
    "imageDemo": "At 6 reads per strand the default codec lost the image in all 5 seeds and the tuned codec recovered it exactly in all 5. The tuned codec spends 0.63 bits per letter instead of 1.19 to do it, and at that same density the default rules also reach the target, so this is the tuned redundancy talking, not the rules."
  },
  "qa": {
    "riskModelRealAuc": "AUC 0.69 on 1,996 held-out real clusters with coverage held fixed. Hold the longest run fixed too and it still reaches 0.61, while the hand rule itself drops to 0.50.",
    "riskTopPatterns": "Nanopore: G and C runs first, GGGGG 0.707 and CCCCC 0.685 against a 0.526 background, and deletion contexts ranked far above substitution contexts. Illumina: every pattern 0.000, nothing worth avoiding.",
    "handRulesOptimalOn": "On Nanopore the run-length rule holds up, and a threshold sweep says the field\u2019s limit of 3 beats 4, 5, 6 and none. We published that against our own earlier claim."
  }
};
