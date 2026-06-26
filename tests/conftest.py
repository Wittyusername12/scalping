"""Shared synthetic fixtures.

The causality / no-lookahead suite runs on small, committed, deterministic
synthetic data -- by design. Proving "the code never peeks at the future" must
not depend on downloaded market data; it must be reproducible offline.

The generated bars are well-formed (High >= max(Open,Close), Low <= min, volume
positive with deliberate open/close spikes) and span multiple sessions with
overnight gaps, so they exercise per-session resets and continuous TR.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from orb import config
from orb.data import aggregate


def _make_day(date: str, start_price: float, seed: int) -> pd.DataFrame:
    """One session of 1-minute RTH bars (09:30-15:59 ET)."""
    idx = pd.date_range(
        f"{date} 09:30", f"{date} 15:59", freq="1min", tz=config.SESSION_TZ
    )
    n = len(idx)
    rng = np.random.default_rng(seed)

    rets = rng.normal(0.0, 0.0005, n)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.0003, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.0003, n)))

    vol = rng.integers(1_000, 5_000, n).astype(float)
    vol[:10] *= 4.0   # opening volume spike
    vol[-10:] *= 4.0  # closing volume spike

    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


@pytest.fixture(scope="session")
def min_bars() -> pd.DataFrame:
    """Three ET sessions of 1-minute bars with overnight gaps (up then down)."""
    days = [
        _make_day("2026-06-22", start_price=500.0, seed=1),  # Monday
        _make_day("2026-06-23", start_price=503.5, seed=2),  # Tuesday (gap up)
        _make_day("2026-06-24", start_price=498.0, seed=3),  # Wednesday (gap down)
    ]
    return pd.concat(days).sort_index()


@pytest.fixture(scope="session")
def bars_15m(min_bars: pd.DataFrame) -> pd.DataFrame:
    """The canonical 9:30-anchored 15-minute series the features run on."""
    return aggregate.resample_15m(min_bars)
