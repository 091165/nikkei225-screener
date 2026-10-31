import numpy as np
import pandas as pd
import pytest

from longterm.config import load_config, override
from longterm.indicators import deviation, ideal_deviation


def _series(values, start="2020-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_deviation_constant_price_is_zero():
    dev = deviation(_series([100.0] * 40), 25)
    assert len(dev) == 40 - 24
    assert np.allclose(dev.values, 0.0)


def test_deviation_formula():
    close = _series([100.0] * 24 + [110.0])
    dev = deviation(close, 25)
    sma = (100.0 * 24 + 110.0) / 25
    assert dev.iloc[-1] == pytest.approx((110.0 - sma) / sma * 100)


def test_ideal_deviation_returns_both_methods():
    cfg = load_config()
    rng = np.random.default_rng(0)
    dev = _series(rng.normal(0.5, 4.0, 200000))
    ideal = ideal_deviation(dev, cfg)

    mean, std = dev.mean(), dev.std()
    assert ideal["sigma"]["lower2"] == pytest.approx(mean - 2 * std)
    assert ideal["sigma"]["upper3"] == pytest.approx(mean + 3 * std)
    # 正規分布なら 2.5% 点 ≈ mean − 1.96σ、0.5% 点 ≈ mean − 2.58σ
    assert ideal["percentile"]["lower2"] == pytest.approx(mean - 1.96 * std, abs=0.15)
    assert ideal["percentile"]["lower3"] == pytest.approx(mean - 2.576 * std, abs=0.25)
    # 既定は percentile 方式
    assert ideal["method"] == "percentile"
    assert ideal["lower2"] == ideal["percentile"]["lower2"]
    assert ideal["stat_days"] == 200000
    assert ideal["start_date"] == "2020-01-01"
    assert ideal["min"] == pytest.approx(dev.min())
    assert ideal["max"] == pytest.approx(dev.max())


def test_ideal_deviation_sigma_mode():
    cfg = override(load_config(), **{"ideal_deviation.method": "sigma"})
    dev = _series(np.random.default_rng(1).normal(0, 3, 1000))
    ideal = ideal_deviation(dev, cfg)
    assert ideal["method"] == "sigma"
    assert ideal["lower2"] == ideal["sigma"]["lower2"]


def test_ideal_deviation_too_short():
    cfg = load_config()
    assert ideal_deviation(_series([0.0] * 50), cfg) is None
