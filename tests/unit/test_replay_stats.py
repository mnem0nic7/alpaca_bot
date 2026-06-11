from alpaca_bot.replay.stats import bootstrap_mean_ci, bootstrap_p_positive


def test_ci_none_below_min_samples():
    assert bootstrap_mean_ci([1.0, 2.0, 3.0, 4.0]) is None
    assert bootstrap_p_positive([1.0, -2.0]) is None


def test_ci_all_positive_excludes_zero():
    values = [5.0, 7.0, 6.0, 8.0, 5.5, 7.5, 6.5, 9.0]
    lo, hi = bootstrap_mean_ci(values)
    assert 0 < lo < hi
    assert bootstrap_p_positive(values) == 0.0


def test_ci_symmetric_values_span_zero():
    values = [10.0, -10.0, 8.0, -8.0, 6.0, -6.0, 4.0, -4.0]
    lo, hi = bootstrap_mean_ci(values)
    assert lo < 0 < hi
    p = bootstrap_p_positive(values)
    assert 0.2 < p < 0.8


def test_deterministic_with_seed():
    values = [1.0, -2.0, 3.0, -1.0, 2.0, 0.5]
    assert bootstrap_mean_ci(values) == bootstrap_mean_ci(values)
    assert bootstrap_p_positive(values) == bootstrap_p_positive(values)


def test_constant_values_degenerate_interval():
    values = [2.0] * 10
    lo, hi = bootstrap_mean_ci(values)
    assert lo == hi == 2.0
