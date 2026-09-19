"""Tests for the alternating loop, on mocks (scripts.run_loop) and a small file for speed."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

import dnacodec.loop as loop
import dnacodec.results as results
from dnacodec.encoder import encode
from dnacodec.loop import (
    Components,
    LoopConfig,
    SearchGrid,
    _call_flexible,
    bits_per_base,
    grid_candidates,
    risk_thresholds,
    run_loop,
    search_settings,
    train_seeds,
)
from dnacodec.profiles import load_profile
from dnacodec.seeds import heldout_seeds, is_heldout
from dnacodec.types import EncoderSettings, Metrics
from scripts.run_loop import MockRiskModel, MockTrialRunner, mock_components, mock_generate_strands

DATA = np.random.default_rng(7).bytes(2000)
NANOPORE = load_profile("nanopore_budget")
ILLUMINA = load_profile("illumina_standard")


def tiny_config(**kw) -> LoopConfig:
    cfg = LoopConfig.quick_mode(workers=1)
    cfg = replace(
        cfg,
        label_strands=90,
        threshold_strands=100,
        coverages=(4, 8, 16, 30),
        curve_coverages=(6, 20),
        curve_trials=2,
        grid=SearchGrid(
            redundancy=(0.1, 0.5, 1.0),
            strand_length=(110, 140),
            max_homopolymer=(3, None),
            gc_rule=(True,),
            risk_quantile=(None, 0.5),
            fallback_redundancy=(1.6,),
        ),
    )
    return replace(cfg, **kw)


@pytest.fixture(autouse=True)
def tmp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(results, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(loop, "CODEC_DIR", tmp_path / "codecs")
    return tmp_path


def run(profile=NANOPORE, comps=None, **kw):
    comps = comps or mock_components()
    cfg = kw.pop("config", tiny_config())
    return run_loop(profile, "t", EncoderSettings(), kw.pop("max_iterations", 3), config=cfg,
                    components=comps, data=DATA, **kw)


def fake_metrics(rate: float, n: int = 1, acc: float = 0.9) -> Metrics:
    return Metrics(n_strands=100 * n, strand_accuracy=acc, mean_edit_distance=0.1, dropout_rate=0.0,
                   reads_per_strand=6.0, per_position_error=[0.0], file_recovered=rate == 1.0,
                   bits_per_base=1.0, recovery_rate=rate, n_trials=n)


# ---------------------------------------------------------------- held-out discipline


def test_search_rejects_heldout_seeds():
    cands = [EncoderSettings()]
    with pytest.raises(AssertionError):
        search_settings(DATA, NANOPORE, None, None, cands, tiny_config(), MockTrialRunner(),
                        heldout_seeds(3), train_seeds(100, 3))
    with pytest.raises(AssertionError):
        search_settings(DATA, NANOPORE, None, None, cands, tiny_config(), MockTrialRunner(),
                        train_seeds(100, 3), heldout_seeds(3))


def test_heldout_seeds_never_reach_the_search(monkeypatch):
    real_search = loop.search_settings
    search_seeds: list[int] = []

    def guarded(*args, **kwargs):
        runner = args[6]

        def check(data, settings, scorer, decoder, profile, seeds, workers=None, **kw):
            assert not any(is_heldout(s) for s in seeds), "held-out seed in the search"
            search_seeds.extend(seeds)
            return runner(data, settings, scorer, decoder, profile, seeds, workers, **kw)

        return real_search(*args[:6], check, *args[7:], **kwargs)

    monkeypatch.setattr(loop, "search_settings", guarded)

    label_seeds, adapt_seeds = [], []
    comps = mock_components()
    base_label = comps.label_failure_rates

    def label(strands, profile, decoder, k, seed, workers=None):
        label_seeds.append(seed)
        return base_label(strands, profile, decoder, k, seed, workers)

    def adapt(decoder, profile, strands, seeds, out_path, steps):
        adapt_seeds.extend(seeds)
        return decoder

    comps.label_failure_rates = label
    comps.adapt = adapt
    runner = comps.recovery_trials
    out = run(comps=comps)
    assert search_seeds and len(out.iterations) >= 1
    assert not any(is_heldout(s) for s in label_seeds + adapt_seeds)
    # Held-out seeds were used, but only in calls made entirely of held-out seeds (evaluation).
    heldout_calls = [seeds for _, seeds, _ in runner.calls if any(is_heldout(s) for s in seeds)]
    assert heldout_calls and all(all(is_heldout(s) for s in seeds) for seeds in heldout_calls)


def test_heldout_results_never_influence_choices():
    def chosen_with(poison_heldout: bool):
        comps = mock_components()
        inner = comps.recovery_trials

        def runner(data, settings, scorer, decoder, profile, seeds, workers=None, **kw):
            m = inner(data, settings, scorer, decoder, profile, seeds, workers, **kw)
            if poison_heldout and all(is_heldout(s) for s in seeds):
                return replace(m, recovery_rate=0.0, file_recovered=False, strand_accuracy=0.0)
            return m

        comps.recovery_trials = runner
        comps = replace(comps, min_reads_at_target=lambda *a, **k: None)
        out = run(comps=comps, config=tiny_config(stop_when_converged=False))
        return [it.settings for it in out.iterations]

    assert chosen_with(False) == chosen_with(True)


# ---------------------------------------------------------------- saving and alternations


def test_saves_after_every_alternation(monkeypatch):
    saved = []
    monkeypatch.setattr(loop, "save_run", lambda r, run_id: saved.append(len(r.iterations)))
    out = run(config=tiny_config(stop_when_converged=False))
    assert len(out.iterations) == 4  # tier 1 + three alternations
    assert saved == [0, 1, 2, 3, 4, 4]  # default, tier 1, one per alternation, final with coverage curve


def test_results_on_disk_after_run():
    out = run(max_iterations=1)
    loaded = results.load_runs("t")
    assert len(loaded) == 1 and loaded[0].situation == NANOPORE.name
    assert loaded[0].is_mock and len(loaded[0].iterations) == len(out.iterations) == 2
    assert loaded[0].n_trials == tiny_config().eval_trials
    manifest = loop.load_codecs("t", NANOPORE.name)
    assert [it["stage"] for it in manifest["iterations"]] == ["rules", "alternation"]
    assert manifest["iterations"][0]["risk"] is None
    assert isinstance(manifest["iterations"][1]["risk"], MockRiskModel)


def test_at_most_three_alternations():
    out = run(max_iterations=5, config=tiny_config(stop_when_converged=False))
    assert len(out.iterations) == 1 + 3  # tier 1 is not an alternation


def test_coverage_curve_names_with_baseline_decoder():
    out = run(max_iterations=1)
    names = {p.decoder for p in out.coverage_curve}
    assert names == {"baseline", "tailored"}
    assert "transformer" in out.iterations[-1].notes  # skipped curve is noted


def test_adapt_gets_default_then_new_encoder_strands():
    comps = mock_components()
    calls = []

    def adapt(decoder, profile, strands, seeds, out_path, steps):
        calls.append(list(strands))
        return decoder

    comps.adapt = adapt
    out = run(comps=comps, max_iterations=2, config=tiny_config(stop_when_converged=False))
    assert len(calls) == 2  # step 1, then step 5 once (not after the last alternation)
    assert calls[0] == encode(DATA, EncoderSettings()).strands
    risk0 = loop.load_codecs("t", NANOPORE.name)["iterations"][1]["risk"]
    assert calls[1] == encode(DATA, out.iterations[1].settings, risk0).strands


# ---------------------------------------------------------------- the search


def formula_runner(passing):
    """Recovery 1.0 iff passing(settings, seeds); records the number of trials run."""
    calls = []

    def runner(data, settings, scorer, decoder, profile, seeds, workers=None, **kw):
        calls.append((settings, list(seeds)))
        ok = passing(settings, list(seeds))
        return fake_metrics(1.0 if ok else 0.0, len(seeds), acc=0.5 + settings.redundancy / 10)

    runner.calls = calls
    return runner


def test_search_picks_highest_bits_per_base_meeting_target():
    grid = SearchGrid(redundancy=(0.1, 0.3, 0.5, 0.8), strand_length=(110, 140), max_homopolymer=(3,),
                      gc_rule=(True,), risk_quantile=(None,))
    cands = grid_candidates(grid, EncoderSettings(), {})
    runner = formula_runner(lambda s, seeds: s.redundancy >= 0.5)
    res = search_settings(DATA, NANOPORE, None, None, cands, tiny_config(), runner,
                          train_seeds(10, 6), train_seeds(20, 12))
    assert res.target_met
    passing = [c for c in cands if c.redundancy >= 0.5]
    best = max(passing, key=lambda c: bits_per_base(DATA, c))
    assert res.chosen == best
    # Levels below the chosen one are never evaluated.
    assert all(bits_per_base(DATA, s) >= bits_per_base(DATA, best) - 1e-12 for s, _ in runner.calls)


def test_chosen_setting_never_fails_train_recheck():
    """A candidate that passes the screen but fails the re-check must not be chosen."""
    recheck = set(train_seeds(20, 12))
    grid = SearchGrid(redundancy=(0.1, 0.3, 0.5), strand_length=(110,), max_homopolymer=(3,),
                      gc_rule=(True,), risk_quantile=(None,))
    cands = grid_candidates(grid, EncoderSettings(), {})

    def passing(s, seeds):
        if s.redundancy == 0.3 and set(seeds) <= recheck:
            return False  # lucky on the screen, fails the re-check
        return s.redundancy >= 0.3

    res = search_settings(DATA, NANOPORE, None, None, cands, tiny_config(), formula_runner(passing),
                          train_seeds(10, 6), sorted(recheck))
    assert res.target_met and res.chosen.redundancy == 0.5
    assert res.chosen_candidate.recheck.recovery_rate == 1.0


def test_chosen_settings_meet_target_on_train_seeds_in_full_loop():
    comps = mock_components()
    cfg = tiny_config(stop_when_converged=False)
    out = run(comps=comps, config=cfg)
    manifest = loop.load_codecs("t", NANOPORE.name)
    for it, rec in zip(out.iterations, manifest["iterations"]):
        assert rec["target_met_train"]
        risk = rec["risk"]
        recheck_seeds = train_seeds(loop.SEED_RECHECK + rec.get("alternation", 0) * 10_000, cfg.recheck_trials)
        m = comps.recovery_trials(DATA, it.settings, risk, None, NANOPORE, recheck_seeds)
        assert m.recovery_rate >= cfg.target


def test_screen_stops_at_first_miss():
    cands = [EncoderSettings(redundancy=0.1)]
    runner = formula_runner(lambda s, seeds: False)
    cfg = tiny_config(screen_trials=50, first_screen_batch=10)
    res = search_settings(DATA, NANOPORE, None, None, cands, cfg, runner, train_seeds(10, 50), train_seeds(100, 20))
    assert sum(len(seeds) for _, seeds in runner.calls) == 10
    assert not res.target_met


def test_no_setting_meets_target_is_recorded_not_crashing():
    comps = mock_components()
    comps.recovery_trials = formula_runner(lambda s, seeds: False)
    comps = replace(comps, min_reads_at_target=lambda *a, **k: None)
    out = run(comps=comps, max_iterations=1)
    assert "TARGET NOT MET" in out.iterations[0].notes
    assert out.iterations[0].min_reads_at_target is None


def test_constraints_too_strict_are_skipped():
    runner = formula_runner(lambda s, seeds: True)
    # GC window that no strand can meet: encode() raises ValueError, the candidate is skipped.
    impossible = EncoderSettings(redundancy=0.1, gc_min=0.9, gc_max=0.95)
    cands = [impossible, EncoderSettings(redundancy=0.3)]
    res = search_settings(DATA, NANOPORE, None, None, cands, tiny_config(), runner,
                          train_seeds(10, 6), train_seeds(20, 12))
    assert res.chosen == cands[1]
    assert res.candidates[0].error
    assert all(s != impossible for s, _ in runner.calls)


def test_search_encodes_each_candidate_once_and_passes_it_on():
    seen = []

    def runner(data, settings, scorer, decoder, profile, seeds, workers=None, *, encoded=None, simulator=None):
        seen.append((settings, id(encoded), encoded.meta.settings == settings))
        return fake_metrics(1.0, len(seeds))

    cands = [EncoderSettings(redundancy=0.3)]
    search_settings(DATA, NANOPORE, None, None, cands, tiny_config(), runner, train_seeds(10, 6), train_seeds(20, 12))
    assert len(seen) == 3  # two screen batches and the re-check
    assert all(ok for _, _, ok in seen)
    assert len({i for _, i, _ in seen[:2]}) == 1  # screen batches share one encoding


# ---------------------------------------------------------------- helpers


def test_bits_per_base_matches_encoder():
    for s in (EncoderSettings(), EncoderSettings(strand_length=140, redundancy=0.7)):
        enc = encode(DATA, s)
        assert bits_per_base(DATA, s) == pytest.approx(len(DATA) * 8 / (len(enc.strands) * s.strand_length))


def test_risk_thresholds_are_quantiles_of_candidates_passing_the_rules():
    strands = mock_generate_strands(300, 110, 1)
    model = MockRiskModel().fit(strands, np.array([min(1.0, s.count("AAAA") * 0.3) for s in strands]))
    grid = SearchGrid(strand_length=(110,), max_homopolymer=(3, None), gc_rule=(True, False),
                      risk_quantile=(None, 0.5, 0.9))
    th = risk_thresholds(model, grid, EncoderSettings(), seed=5, n=600)
    assert len(th) == 1 * 2 * 2 * 3
    assert all(th[(110, hp, gc, None)] is None for hp in (3, None) for gc in (True, False))
    for hp in (3, None):
        for gc in (True, False):
            assert th[(110, hp, gc, 0.5)] <= th[(110, hp, gc, 0.9)]
    # With the rule on, the pool only holds rule-abiding strands, so the risk of the
    # threshold's pool differs from the unconstrained one.
    assert th[(110, 3, True, 0.5)] != th[(110, None, False, 0.5)]


def test_grid_candidates_turn_rules_off():
    grid = SearchGrid(redundancy=(0.2,), strand_length=(110,), max_homopolymer=(3, None), gc_rule=(True, False),
                      risk_quantile=(None, 0.5))
    th = {(110, hp, gc, q): (None if q is None else 0.4) for hp in (3, None) for gc in (True, False) for q in (None, 0.5)}
    cands = grid_candidates(grid, EncoderSettings(), th)
    assert len(cands) == 8
    assert any(c.max_homopolymer is None and c.gc_min is None and c.risk_threshold == 0.4 for c in cands)


def test_call_flexible_matches_names():
    def f(strands, k, seed=0):
        return (len(strands), k, seed)

    assert _call_flexible(f, strands=["A"], k=3, K=3, seed=9, workers=2) == (1, 3, 9)
    with pytest.raises(TypeError):
        _call_flexible(f, k=3)


def test_real_components_default_to_lazy_imports():
    comps = Components().resolved()
    assert comps.recovery_trials is loop._real_recovery_trials
    assert comps.adapt is loop.default_adapt
    # The baseline decoder has no checkpoint, so adapting it is a no-op.
    dec = comps.baseline_decoder()
    assert loop.default_adapt(dec, NANOPORE, ["ACGT" * 20], [1], None, 10) is dec


def test_tier1_uses_rule_scorer_and_same_seeds_as_first_alternation(monkeypatch):
    real_search = loop.search_settings
    seen = []

    def spy(data, profile, scorer, decoder, candidates, config, runner, screen, recheck, **kw):
        seen.append((scorer, [c.risk_threshold for c in candidates], list(screen), list(recheck), decoder))
        return real_search(data, profile, scorer, decoder, candidates, config, runner, screen, recheck, **kw)

    monkeypatch.setattr(loop, "search_settings", spy)
    run(max_iterations=1)
    (s_c, thr_c, scr_c, rc_c, dec_c), (s_d, thr_d, scr_d, rc_d, dec_d) = seen
    assert s_c is None and all(t is None for t in thr_c)  # C: rule scorer, no risk threshold
    assert isinstance(s_d, MockRiskModel)  # D: learned scorer
    assert scr_c == scr_d and rc_c == rc_d and dec_c is dec_d  # same seeds, same frozen decoder
    # Same grid apart from the risk threshold.
    assert {c for c in thr_d if c is None} == {None}


def test_gpu_decoders_label_in_process(monkeypatch):
    import dnacodec.risk as risk

    seen = {}

    def fake(strands, decoder, profile, k=16, seed=0, workers=None, heldout=False):
        seen["workers"] = workers
        return np.zeros(len(strands))

    monkeypatch.setattr(risk, "label_failure_rates", fake)

    class GpuDecoder:
        name = "transformer"
        main_process_only = True

    loop._real_label(["ACGT" * 10], NANOPORE, GpuDecoder(), 2, 1, workers=8)
    assert seen["workers"] == 1
    loop._real_label(["ACGT" * 10], NANOPORE, object(), 2, 1, workers=8)
    assert seen["workers"] == 8


def test_iterations_carry_stage_and_matched_density():
    comps = mock_components()
    out = run(comps=comps, max_iterations=2, config=tiny_config(stop_when_converged=False))
    assert [it.stage for it in out.iterations] == ["tier1", "alternation 0", "alternation 1"]
    for it in out.iterations:
        matched = loop.matched_default(EncoderSettings(), it.settings)
        expected = comps.min_reads_at_target(DATA, matched, None, None, NANOPORE, heldout_seeds(tiny_config().eval_trials),
                                             1.0, tiny_config().coverages)
        assert it.default_min_reads_matched == expected
        assert "min reads" not in it.notes.split("matched-density default")[-1]
    loaded = results.load_runs("t")[0]
    assert [it.stage for it in loaded.iterations] == [it.stage for it in out.iterations]
    assert [it.default_min_reads_matched for it in loaded.iterations] == [it.default_min_reads_matched for it in out.iterations]
