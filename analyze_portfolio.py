#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import warnings
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import quantstats as qs
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import AverageTrueRange

from evostock_paths import config_path, data_path
from rebuild_holdings_from_broker_events import (
    ReconciliationError,
    resolve_committed_path,
    verify_commit_manifest,
)
from append_candidate_event import CandidateEventError, validate_candidate_watchlist
from apply_chat_holdings_overlay import OverlayError, later_position_event_ids, verify_overlay


BENCHMARKS = ["SPY", "QQQ", "IWM", "SMH", "SOXX", "IGV", "RSP", "HYG", "IEF", "^VIX"]
ET = ZoneInfo("America/New_York")
DAILY_BAR_READY_AT = time(16, 15)
MARKET_LABELS = {
    "SPY": ("S&P 500", "大盘"),
    "QQQ": ("Nasdaq 100", "科技/成长"),
    "IWM": ("Russell 2000", "小盘风险偏好"),
    "SMH": ("VanEck Semiconductor ETF", "半导体"),
    "SOXX": ("iShares Semiconductor ETF", "半导体备选"),
    "IGV": ("iShares Expanded Tech-Software ETF", "软件"),
    "RSP": ("Invesco S&P 500 Equal Weight ETF", "市场广度"),
    "HYG": ("iShares High Yield Corporate Bond ETF", "信用风险偏好"),
    "IEF": ("iShares 7-10 Year Treasury Bond ETF", "中期国债基准"),
    "^VIX": ("CBOE Volatility Index", "波动率"),
}
DEFAULT_OPENING_UNIVERSE = [
    {"ticker": "MSFT", "company_name": "Microsoft", "theme": "AI software / cloud"},
    {"ticker": "NVDA", "company_name": "NVIDIA", "theme": "AI accelerators"},
    {"ticker": "AVGO", "company_name": "Broadcom", "theme": "AI networking / custom silicon"},
    {"ticker": "AMZN", "company_name": "Amazon", "theme": "cloud / consumer"},
    {"ticker": "GOOGL", "company_name": "Alphabet", "theme": "search / cloud / AI"},
    {"ticker": "META", "company_name": "Meta Platforms", "theme": "AI advertising"},
    {"ticker": "TSM", "company_name": "Taiwan Semiconductor", "theme": "advanced foundry"},
    {"ticker": "ASML", "company_name": "ASML", "theme": "semiconductor equipment"},
    {"ticker": "AMD", "company_name": "Advanced Micro Devices", "theme": "AI accelerators"},
    {"ticker": "ANET", "company_name": "Arista Networks", "theme": "AI networking"},
    {"ticker": "PANW", "company_name": "Palo Alto Networks", "theme": "cybersecurity"},
    {"ticker": "CRWD", "company_name": "CrowdStrike", "theme": "cybersecurity"},
    {"ticker": "NOW", "company_name": "ServiceNow", "theme": "enterprise software"},
    {"ticker": "PLTR", "company_name": "Palantir", "theme": "AI software"},
    {"ticker": "ARM", "company_name": "Arm Holdings", "theme": "compute IP"},
]


@dataclass
class QuoteMetrics:
    ticker: str
    close: float | None
    ret_1d_pct: float | None
    ret_5d_pct: float | None
    ret_20d_pct: float | None
    ret_60d_pct: float | None
    vol_30d_ann_pct: float | None
    max_drawdown_1y_pct: float | None
    sma_20_gap_pct: float | None
    sma_50_gap_pct: float | None
    sma_200_gap_pct: float | None
    rsi_14: float | None
    macd_hist: float | None
    atr_14_pct: float | None
    volume_ratio_20d: float | None
    beta_spy: float | None
    rel_spy_20d_pct: float | None
    rel_spy_60d_pct: float | None
    data_note: str = ""
    price_as_of: str = ""
    observation_count: int = 0
    current_drawdown_1y_pct: float | None = None
    max_drawdown_60d_pct: float | None = None
    var_95_1d_pct: float | None = None
    cvar_95_1d_pct: float | None = None
    worst_1d_pct: float | None = None


def empty_metrics(ticker: str, note: str = "missing price data") -> QuoteMetrics:
    return QuoteMetrics(
        ticker=ticker,
        close=None,
        ret_1d_pct=None,
        ret_5d_pct=None,
        ret_20d_pct=None,
        ret_60d_pct=None,
        vol_30d_ann_pct=None,
        max_drawdown_1y_pct=None,
        sma_20_gap_pct=None,
        sma_50_gap_pct=None,
        sma_200_gap_pct=None,
        rsi_14=None,
        macd_hist=None,
        atr_14_pct=None,
        volume_ratio_20d=None,
        beta_spy=None,
        rel_spy_20d_pct=None,
        rel_spy_60d_pct=None,
        data_note=note,
    )


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    return pd.read_csv(path)


def read_opening_universe(path: Path) -> pd.DataFrame:
    if path.exists():
        universe = read_table(path)
    else:
        universe = pd.DataFrame(DEFAULT_OPENING_UNIVERSE)
    if universe.empty or "ticker" not in universe.columns:
        return pd.DataFrame(DEFAULT_OPENING_UNIVERSE)
    universe = universe.copy()
    universe["ticker"] = universe["ticker"].map(clean_ticker)
    universe = universe[universe["ticker"] != ""]
    return universe.drop_duplicates(subset=["ticker"], keep="first")


def clean_ticker(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().upper()


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text == "待确认":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def clean_text(value: Any, default: str = "未填写") -> str:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip()
    return text if text else default


def pct(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{value:.{digits}f}%"


def num(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{value:.{digits}f}"


def money(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"${value:,.2f}"


def market_phase(now_et: datetime) -> str:
    clock = now_et.timetz().replace(tzinfo=None)
    if time(4, 0) <= clock < time(9, 30):
        return "盘前"
    if time(9, 30) <= clock < time(16, 0):
        return "常规盘"
    if time(16, 0) <= clock < time(20, 0):
        return "盘后"
    return "休市时段"


def extract_price_frames(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    if raw.empty:
        return frames

    if isinstance(raw.columns, pd.MultiIndex):
        level0 = [str(x).upper() for x in raw.columns.get_level_values(0)]
        level1 = [str(x) for x in raw.columns.get_level_values(1)]
        for ticker in tickers:
            if ticker in level0:
                frame = raw[ticker].copy()
                frame.columns = [str(c).title() for c in frame.columns]
                frames[ticker] = frame.dropna(how="all")
            elif ticker in [x.upper() for x in level1]:
                frame = raw.xs(ticker, level=1, axis=1).copy()
                frame.columns = [str(c).title() for c in frame.columns]
                frames[ticker] = frame.dropna(how="all")
    else:
        if len(tickers) == 1:
            frame = raw.copy()
            frame.columns = [str(c).title() for c in frame.columns]
            frames[tickers[0]] = frame.dropna(how="all")

    return frames


def session_date(value: Any) -> Any:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(ET)
    return timestamp.date()


def keep_completed_daily_bars(frame: pd.DataFrame, now_et: datetime) -> pd.DataFrame:
    if frame.empty or now_et.timetz().replace(tzinfo=None) >= DAILY_BAR_READY_AT:
        return frame
    current_session = now_et.date()
    mask = [session_date(value) < current_session for value in frame.index]
    return frame.loc[mask]


def download_prices(
    tickers: list[str], period: str, now_et: datetime | None = None
) -> dict[str, pd.DataFrame]:
    unique = sorted({t for t in tickers if t and t != "CASH"})
    if not unique:
        return {}
    raw = yf.download(
        tickers=unique,
        period=period,
        interval="1d",
        auto_adjust=True,
        group_by="ticker",
        threads=False,
        progress=False,
    )
    frames = extract_price_frames(raw, unique)
    effective_now = now_et or datetime.now(ET)
    return {
        ticker: keep_completed_daily_bars(frame, effective_now)
        for ticker, frame in frames.items()
    }


EXAMPLE_DEMO_HOLDINGS = "examples/demo_portfolio.csv"
DEMO_BANNER = (
    "> **DEMO MODE — synthetic data.**\n"
    "> Positions and prices below are generated locally from a fixed seed. They are\n"
    "> not market data, not a real portfolio, and not investment advice. This run\n"
    "> exists to show what the analysis pipeline produces end to end.\n"
    "> Run without `--demo` to analyse your own verified holdings.\n\n"
)

DEMO_MARKET_VOL = 0.0079
# ticker -> (closing price on the final synthetic session, beta to SPY,
#            idiosyncratic daily vol, annualised drift)
DEMO_SERIES = {
    "SPY": (592.41, 1.00, 0.0000, 0.09),
    "QQQ": (511.68, 1.12, 0.0036, 0.13),
    "IWM": (221.34, 1.06, 0.0055, 0.04),
    "SMH": (257.83, 1.44, 0.0090, 0.18),
    "SOXX": (237.62, 1.41, 0.0088, 0.17),
    "IGV": (98.76, 1.17, 0.0064, 0.11),
    "RSP": (179.45, 0.92, 0.0029, 0.06),
    "HYG": (79.38, 0.27, 0.0022, 0.03),
    "IEF": (95.24, -0.06, 0.0025, 0.02),
    "^VIX": (16.47, -6.40, 0.0380, 0.00),
    "MSFT": (423.86, 1.04, 0.0091, 0.12),
    "NVDA": (141.72, 1.61, 0.0166, 0.22),
    "AVGO": (230.44, 1.34, 0.0129, 0.19),
    "GOOGL": (175.63, 1.01, 0.0104, 0.10),
}


def demo_series_params(ticker: str) -> tuple[float, float, float, float]:
    if ticker in DEMO_SERIES:
        return DEMO_SERIES[ticker]
    rng = random.Random(f"evostock-demo-params:{ticker}")
    return (
        round(rng.uniform(45.0, 380.0), 2),
        round(rng.uniform(0.85, 1.55), 2),
        round(rng.uniform(0.009, 0.019), 4),
        round(rng.uniform(0.02, 0.20), 3),
    )


def demo_price_frames(
    tickers: list[str], sessions: int = 320, anchor: date | None = None
) -> dict[str, pd.DataFrame]:
    """Deterministic synthetic daily bars, so ``--demo`` needs no network.

    A single seeded market factor drives every series through a per-ticker
    beta, so cross-sectional readings (relative strength, breadth, a VIX that
    rises when the market falls) stay internally consistent instead of being
    independent noise. Each path is then rescaled to a fixed closing price, so
    the same run produces the same numbers on every machine.

    These are not market prices and must never be read as one. They exist to
    exercise the analysis pipeline end to end without a network round trip.
    """
    unique = sorted({t for t in tickers if t and t != "CASH"})
    if not unique:
        return {}

    last_session = (anchor or datetime.now(ET).date()) - timedelta(days=1)
    index = pd.bdate_range(end=pd.Timestamp(last_session), periods=sessions)

    market_rng = random.Random("evostock-demo:market-factor")
    market = [market_rng.gauss(0.0, DEMO_MARKET_VOL) for _ in range(sessions)]

    frames: dict[str, pd.DataFrame] = {}
    for ticker in unique:
        last_price, beta, idio_vol, drift = demo_series_params(ticker)
        rng = random.Random(f"evostock-demo:{ticker}")
        daily_drift = drift / 252.0

        path = [1.0]
        for i in range(sessions):
            step = daily_drift + beta * market[i] + rng.gauss(0.0, idio_vol)
            if ticker == "^VIX":  # pull VIX back toward its long-run level
                step += 0.045 * (1.0 - path[-1])
            path.append(max(path[-1] * (1.0 + step), 1e-6))
        path = path[1:]

        scale = last_price / path[-1]
        closes = [round(value * scale, 4) for value in path]

        rows = []
        for close in closes:
            spread = close * rng.uniform(0.003, 0.014)
            open_ = round(close * (1.0 + rng.gauss(0.0, 0.0035)), 4)
            rows.append(
                {
                    "Open": open_,
                    "High": round(max(open_, close) + spread * rng.random(), 4),
                    "Low": round(min(open_, close) - spread * rng.random(), 4),
                    "Close": close,
                    "Volume": float(int(rng.uniform(8e5, 4.5e7))),
                }
            )
        frames[ticker] = pd.DataFrame(rows, index=index)

    return frames


def total_return(close: pd.Series, days: int) -> float | None:
    s = close.dropna()
    if len(s) <= days:
        return None
    return (float(s.iloc[-1]) / float(s.iloc[-days - 1]) - 1.0) * 100.0


def annualized_vol(close: pd.Series, days: int) -> float | None:
    returns = close.pct_change().dropna().tail(days)
    if len(returns) < max(5, days // 3):
        return None
    return float(returns.std() * math.sqrt(252) * 100.0)


def max_drawdown(close: pd.Series) -> float | None:
    s = close.dropna()
    if s.empty:
        return None
    dd = s / s.cummax() - 1.0
    return float(dd.min() * 100.0)


def current_drawdown(close: pd.Series) -> float | None:
    s = close.dropna()
    if s.empty:
        return None
    peak = float(s.cummax().iloc[-1])
    last = float(s.iloc[-1])
    if not peak:
        return None
    return (last / peak - 1.0) * 100.0


def quant_risk_metrics(close: pd.Series) -> tuple[float | None, float | None, float | None]:
    returns = close.pct_change().dropna()
    if len(returns) < 20:
        return None, None, None
    if not isinstance(returns.index, pd.DatetimeIndex):
        returns.index = pd.to_datetime(returns.index)
    trailing = returns.tail(252)
    worst_1d = float(trailing.min() * 100.0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            var_95 = float(
                qs.stats.value_at_risk(trailing, confidence=0.95, prepare_returns=False)
                * 100.0
            )
        if not math.isfinite(var_95):
            raise ValueError
    except Exception:
        var_95 = float(trailing.quantile(0.05) * 100.0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            cvar_95 = float(
                qs.stats.conditional_value_at_risk(
                    trailing, confidence=0.95, prepare_returns=False
                )
                * 100.0
            )
        if not math.isfinite(cvar_95):
            raise ValueError
    except Exception:
        cutoff = trailing.quantile(0.05)
        tail = trailing[trailing <= cutoff]
        cvar_95 = float(tail.mean() * 100.0) if not tail.empty else None
    return var_95, cvar_95, worst_1d


def beta_to_spy(close: pd.Series, spy_close: pd.Series | None) -> float | None:
    if spy_close is None or spy_close.empty:
        return None
    joined = pd.concat([close.pct_change(), spy_close.pct_change()], axis=1, sort=False).dropna()
    if len(joined) < 40:
        return None
    cov = joined.iloc[:, 0].cov(joined.iloc[:, 1])
    var = joined.iloc[:, 1].var()
    if not var or not math.isfinite(var):
        return None
    return float(cov / var)


def build_metrics(ticker: str, frame: pd.DataFrame, spy_close: pd.Series | None) -> QuoteMetrics:
    required = {"Close"}
    if frame.empty or not required.issubset(frame.columns):
        return empty_metrics(ticker)

    close = frame["Close"].dropna()
    if close.empty:
        return empty_metrics(ticker, "missing close")

    high = frame["High"] if "High" in frame.columns else close
    low = frame["Low"] if "Low" in frame.columns else close
    volume = frame["Volume"] if "Volume" in frame.columns else pd.Series(dtype=float)

    last = float(close.iloc[-1])
    ret_1d = total_return(close, 1)
    ret_5d = total_return(close, 5)
    ret_20d = total_return(close, 20)
    ret_60d = total_return(close, 60)
    vol_30d = annualized_vol(close, 30)
    mdd = max_drawdown(close)
    current_dd = current_drawdown(close)
    max_dd_60d = max_drawdown(close.tail(60))
    var_95, cvar_95, worst_1d = quant_risk_metrics(close)

    def gap_to_sma(days: int) -> float | None:
        if len(close) < days:
            return None
        sma = float(close.tail(days).mean())
        if not sma:
            return None
        return (last / sma - 1.0) * 100.0

    rsi = None
    macd_hist = None
    atr_pct = None
    try:
        rsi = float(RSIIndicator(close=close, window=14).rsi().dropna().iloc[-1])
    except Exception:
        pass
    try:
        macd_hist = float(MACD(close=close).macd_diff().dropna().iloc[-1])
    except Exception:
        pass
    try:
        atr = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range().dropna()
        if not atr.empty and last:
            atr_pct = float(atr.iloc[-1] / last * 100.0)
    except Exception:
        pass

    volume_ratio = None
    if len(volume.dropna()) >= 21:
        recent_avg = float(volume.dropna().tail(21).iloc[:-1].mean())
        if recent_avg:
            volume_ratio = float(volume.dropna().iloc[-1] / recent_avg)

    spy_20 = total_return(spy_close, 20) if spy_close is not None else None
    spy_60 = total_return(spy_close, 60) if spy_close is not None else None

    rel_20 = ret_20d - spy_20 if ret_20d is not None and spy_20 is not None else None
    rel_60 = ret_60d - spy_60 if ret_60d is not None and spy_60 is not None else None

    return QuoteMetrics(
        ticker=ticker,
        close=last,
        ret_1d_pct=ret_1d,
        ret_5d_pct=ret_5d,
        ret_20d_pct=ret_20d,
        ret_60d_pct=ret_60d,
        vol_30d_ann_pct=vol_30d,
        max_drawdown_1y_pct=mdd,
        sma_20_gap_pct=gap_to_sma(20),
        sma_50_gap_pct=gap_to_sma(50),
        sma_200_gap_pct=gap_to_sma(200),
        rsi_14=rsi,
        macd_hist=macd_hist,
        atr_14_pct=atr_pct,
        volume_ratio_20d=volume_ratio,
        beta_spy=beta_to_spy(close, spy_close),
        rel_spy_20d_pct=rel_20,
        rel_spy_60d_pct=rel_60,
        price_as_of=session_date(close.index[-1]).isoformat(),
        observation_count=len(close),
        current_drawdown_1y_pct=current_dd,
        max_drawdown_60d_pct=max_dd_60d,
        var_95_1d_pct=var_95,
        cvar_95_1d_pct=cvar_95,
        worst_1d_pct=worst_1d,
    )


def technical_state(m: QuoteMetrics) -> str:
    if m.close is None:
        return "数据不足"
    if m.observation_count < 21 or m.ret_20d_pct is None or m.rsi_14 is None:
        return "数据不足"
    weak = (m.sma_50_gap_pct is not None and m.sma_50_gap_pct < -5) or (m.sma_200_gap_pct is not None and m.sma_200_gap_pct < -3)
    strong = (m.sma_50_gap_pct is not None and m.sma_50_gap_pct > 3) and (m.rel_spy_20d_pct is not None and m.rel_spy_20d_pct > 0)
    overbought = m.rsi_14 is not None and m.rsi_14 >= 75
    oversold = m.rsi_14 is not None and m.rsi_14 <= 30
    if weak:
        return "趋势转弱/需复核"
    if overbought:
        return "偏热/不宜追高"
    if oversold:
        return "超卖/只作风险提示"
    if strong:
        return "趋势健康"
    return "趋势中性"


def market_regime(metrics: dict[str, QuoteMetrics]) -> tuple[str, list[str]]:
    spy = metrics.get("SPY")
    qqq = metrics.get("QQQ")
    smh = metrics.get("SMH") or metrics.get("SOXX")
    vix = metrics.get("^VIX")
    reasons: list[str] = []

    required = {"SPY": spy, "QQQ": qqq, "IWM": metrics.get("IWM"), "IGV": metrics.get("IGV"), "VIX": vix}
    missing = [
        name
        for name, metric in required.items()
        if metric is None
        or metric.close is None
        or (name != "VIX" and metric.ret_20d_pct is None)
    ]
    if smh is None or smh.close is None or smh.ret_20d_pct is None:
        missing.append("SMH/SOXX")
    if missing:
        return "市场数据不足/待确认", [f"缺少一致的已完成日线：{', '.join(missing)}"]

    vix_close = vix.close if vix and vix.close is not None else None
    if spy and spy.ret_20d_pct is not None:
        reasons.append(f"SPY 20日 {pct(spy.ret_20d_pct)}")
    if qqq and qqq.ret_20d_pct is not None:
        reasons.append(f"QQQ 20日 {pct(qqq.ret_20d_pct)}")
    if smh and smh.rel_spy_20d_pct is not None:
        reasons.append(f"半导体相对SPY20日 {pct(smh.rel_spy_20d_pct)}")
    if vix_close is not None:
        reasons.append(f"VIX {num(vix_close, 1)}")

    risk_off = any(
        [
            spy and spy.ret_20d_pct is not None and spy.ret_20d_pct <= -5,
            qqq and qqq.ret_20d_pct is not None and qqq.ret_20d_pct <= -7,
            spy and spy.sma_50_gap_pct is not None and spy.sma_50_gap_pct <= -3,
            qqq and qqq.sma_50_gap_pct is not None and qqq.sma_50_gap_pct <= -4,
            vix_close is not None and vix_close >= 22,
        ]
    )
    risk_on = all(
        [
            spy and spy.sma_50_gap_pct is not None and spy.sma_50_gap_pct > 0,
            qqq and qqq.sma_50_gap_pct is not None and qqq.sma_50_gap_pct > 0,
            vix_close is None or vix_close < 20,
        ]
    ) and (smh is None or smh.ret_20d_pct is None or smh.ret_20d_pct > 0)

    if risk_off:
        return "风险偏好转弱/先控回撤", reasons
    if risk_on:
        return "风险偏好偏强/顺风", reasons
    return "市场分化/中性", reasons


def market_heat(metrics: dict[str, QuoteMetrics]) -> tuple[str, list[str]]:
    spy = metrics.get("SPY")
    qqq = metrics.get("QQQ")
    iwm = metrics.get("IWM")
    smh = metrics.get("SMH") or metrics.get("SOXX")
    rsp = metrics.get("RSP")
    hyg = metrics.get("HYG")
    ief = metrics.get("IEF")
    vix = metrics.get("^VIX")

    score = 0
    reasons: list[str] = []

    required = {"SPY": spy, "QQQ": qqq, "IWM": iwm, "RSP": rsp, "HYG": hyg, "IEF": ief, "VIX": vix}
    missing = [
        name
        for name, metric in required.items()
        if metric is None
        or metric.close is None
        or (name != "VIX" and metric.ret_20d_pct is None)
    ]
    if smh is None or smh.close is None or smh.rel_spy_20d_pct is None:
        missing.append("SMH/SOXX")
    if missing:
        return "待确认", [f"缺少热度输入：{', '.join(missing)}"]

    if spy and spy.ret_20d_pct is not None:
        score += 1 if spy.ret_20d_pct > 0 else -1
    if qqq and qqq.ret_20d_pct is not None:
        score += 1 if qqq.ret_20d_pct > 0 else -1

    if rsp and rsp.rel_spy_20d_pct is not None:
        breadth = rsp.rel_spy_20d_pct
        score += 1 if breadth >= 0 else -1
        reasons.append(f"RSP相对SPY20日 {pct(breadth)}")

    if iwm and iwm.rel_spy_20d_pct is not None:
        small_cap = iwm.rel_spy_20d_pct
        score += 1 if small_cap >= 0 else -1
        reasons.append(f"IWM相对SPY20日 {pct(small_cap)}")

    if hyg and ief and hyg.ret_20d_pct is not None and ief.ret_20d_pct is not None:
        credit_spread_proxy = hyg.ret_20d_pct - ief.ret_20d_pct
        score += 1 if credit_spread_proxy >= 0 else -1
        reasons.append(f"HYG相对IEF20日 {pct(credit_spread_proxy)}")

    if smh and smh.rel_spy_20d_pct is not None:
        score += 1 if smh.rel_spy_20d_pct >= 0 else -1

    vix_close = vix.close if vix and vix.close is not None else None
    if vix_close is not None:
        if vix_close >= 22:
            score -= 2
        elif vix_close >= 18:
            score -= 1
        elif vix_close < 16:
            score += 1
        reasons.append(f"VIX {num(vix_close, 1)}")
    if vix and vix.ret_5d_pct is not None and vix.ret_5d_pct >= 20:
        score -= 1
        reasons.append(f"VIX 5日 {pct(vix.ret_5d_pct)}")

    hot_rsi_count = sum(
        metric is not None and metric.rsi_14 is not None and metric.rsi_14 >= 70
        for metric in (spy, qqq, smh)
    )
    if score >= 4 and hot_rsi_count >= 2 and (vix_close is None or vix_close < 16):
        return "过热", reasons
    if score >= 4:
        return "偏热", reasons
    if score >= 2:
        return "中性偏热", reasons
    if score >= 0:
        return "中性", reasons
    if score >= -2:
        return "偏冷", reasons
    return "风险规避", reasons


def market_environment_rows(metrics: dict[str, QuoteMetrics]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ticker in BENCHMARKS:
        metric = metrics.get(ticker)
        if metric is None or metric.close is None:
            continue
        name, role = MARKET_LABELS.get(ticker, (ticker, "市场"))
        rows.append(
            {
                "标的": ticker,
                "名称": name,
                "用途": role,
                "价格": num(metric.close, 2),
                "数据截止": metric.price_as_of or "待确认",
                "1日": pct(metric.ret_1d_pct),
                "5日": pct(metric.ret_5d_pct),
                "20日": pct(metric.ret_20d_pct),
                "60日": pct(metric.ret_60d_pct),
                "相对SPY20日": "基准" if ticker == "SPY" else pct(metric.rel_spy_20d_pct),
                "RSI": num(metric.rsi_14, 1),
                "状态": "波动率" if ticker == "^VIX" else technical_state(metric),
            }
        )
    return rows


def candidate_score(m: QuoteMetrics) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if m.close is None or technical_state(m) == "数据不足":
        return -99, ["完成日线历史不足"]
    if m.sma_50_gap_pct is not None and m.sma_50_gap_pct > 0:
        score += 1
        reasons.append("高于50日均线")
    if m.sma_200_gap_pct is not None and m.sma_200_gap_pct > 0:
        score += 1
        reasons.append("高于200日均线")
    if m.ret_60d_pct is not None and m.ret_60d_pct > 0:
        score += 1
        reasons.append("60日收益为正")
    if m.rel_spy_20d_pct is not None and m.rel_spy_20d_pct > 0:
        score += 1
        reasons.append("20日相对SPY更强")
    if m.rsi_14 is not None and 45 <= m.rsi_14 <= 70:
        score += 1
        reasons.append("RSI未过热")
    if m.vol_30d_ann_pct is not None and m.vol_30d_ann_pct <= 75:
        score += 1
        reasons.append("波动未极端")
    if m.max_drawdown_1y_pct is not None and m.max_drawdown_1y_pct > -45:
        score += 1
        reasons.append("一年回撤未极端")
    if m.rsi_14 is not None and m.rsi_14 > 78:
        score -= 2
        reasons.append("RSI过热扣分")
    if m.sma_200_gap_pct is not None and m.sma_200_gap_pct < -8:
        score -= 2
        reasons.append("低于200日均线较多")
    return score, reasons


def opening_candidate_score(m: QuoteMetrics) -> tuple[int, list[str], list[str]]:
    score, reasons = candidate_score(m)
    risks: list[str] = []
    if m.close is None:
        return -99, reasons, ["无行情数据"]
    if m.rsi_14 is not None and m.rsi_14 >= 75:
        risks.append("RSI偏热")
    if m.vol_30d_ann_pct is not None and m.vol_30d_ann_pct > 90:
        risks.append("30日波动过高")
    if m.max_drawdown_1y_pct is not None and m.max_drawdown_1y_pct <= -50:
        risks.append("一年最大回撤深")
    if m.current_drawdown_1y_pct is not None and m.current_drawdown_1y_pct > -2:
        risks.append("接近一年最高收盘")
    if m.sma_50_gap_pct is not None and m.sma_50_gap_pct < 0:
        risks.append("低于50日均线")
    if m.rel_spy_20d_pct is not None and m.rel_spy_20d_pct < 0:
        risks.append("20日弱于SPY")
    return score, reasons, risks


def build_opening_candidate_rows(
    universe: pd.DataFrame,
    metrics: dict[str, QuoteMetrics],
    held_tickers: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in universe.iterrows():
        ticker = clean_ticker(row.get("ticker"))
        if not ticker or ticker in held_tickers or ticker in BENCHMARKS:
            continue
        metric = metrics.get(ticker)
        if metric is None or metric.close is None:
            continue
        score, reasons, risks = opening_candidate_score(metric)
        if score < 5:
            continue
        severe_risk = any(r in risks for r in {"RSI偏热", "30日波动过高", "低于50日均线", "20日弱于SPY"})
        if severe_risk:
            continue
        rows.append(
            {
                "Ticker": ticker,
                "公司": clean_text(row.get("company_name"), ticker),
                "主题": clean_text(row.get("theme"), "待确认"),
                "价格": money(metric.close),
                "数据截止": metric.price_as_of or "待确认",
                "20日": pct(metric.ret_20d_pct),
                "60日": pct(metric.ret_60d_pct),
                "相对SPY20日": pct(metric.rel_spy_20d_pct),
                "RSI": num(metric.rsi_14, 1),
                "候选分": score,
                "量化理由": "; ".join(reasons[:3]),
                "风险核验": "; ".join(risks[:3]) if risks else "新闻/财报/估值待确认",
            }
        )
    rows = sorted(
        rows,
        key=lambda x: (
            safe_float(x.get("候选分"), -99),
            safe_float(str(x.get("相对SPY20日", "NA")).replace("%", ""), -99),
            safe_float(str(x.get("20日", "NA")).replace("%", ""), -99),
        ),
        reverse=True,
    )
    return rows[: max(limit, 0)]


def build_persistent_candidate_rows(
    candidate_watchlist: pd.DataFrame, metrics: dict[str, QuoteMetrics]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in candidate_watchlist.iterrows():
        ticker = clean_ticker(row.get("ticker"))
        if not ticker:
            continue
        metric = metrics.get(ticker) or empty_metrics(ticker, "missing")
        technical_score, _, technical_risks = opening_candidate_score(metric)
        selection_score = safe_float(row.get("selection_score"), -1)
        coverage = safe_float(row.get("coverage_pct"), -1)
        rows.append(
            {
                "Ticker": ticker,
                "状态": clean_text(row.get("state"), "研究队列"),
                "全维分": num(selection_score, 0) if selection_score >= 0 else "待确认",
                "覆盖率": pct(coverage, 0) if coverage >= 0 else "待确认",
                "价格": money(metric.close),
                "数据截止": metric.price_as_of or "待确认",
                "1日": pct(metric.ret_1d_pct),
                "20日": pct(metric.ret_20d_pct),
                "相对SPY20日": pct(metric.rel_spy_20d_pct),
                "技术预筛": f"{technical_score}/7" if technical_score >= 0 else "NA",
                "技术风险": "; ".join(technical_risks[:2]) if technical_risks else "无明显技术硬门槛",
                "下个事件": clean_text(row.get("next_event_at"), "待确认"),
                "入场条件": clean_text(row.get("entry_condition"), "待确认"),
                "失效条件": clean_text(row.get("invalidation_condition"), "待确认"),
            }
        )
    return rows


def action_for_holding(row: pd.Series, m: QuoteMetrics, pnl_pct: float | None, market_signal: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if m.close is None:
        return "观望但提高警戒", ["缺少可靠行情数据"]

    stop_loss = optional_float(row.get("stop_loss_pct"))
    trim_profit = optional_float(row.get("trim_profit_pct"))

    if pnl_pct is not None and stop_loss is not None and pnl_pct <= stop_loss:
        reasons.append(f"浮亏 {pnl_pct:.1f}% 触及用户明确的止损/复核线 {stop_loss:.1f}%")
        return "卖出审查", reasons

    state = technical_state(m)
    if state == "数据不足":
        return "观望但提高警戒", ["完成日线历史不足，不能生成趋势动作"]
    if market_signal == "市场数据不足/待确认":
        return "观望但提高警戒", ["市场环境数据不足，暂停新增方向性动作"]

    if m.sma_200_gap_pct is not None and m.sma_200_gap_pct < -5 and m.rel_spy_60d_pct is not None and m.rel_spy_60d_pct < -5:
        reasons.append("跌破200日均线且60日弱于SPY")
        return "观望但提高警戒", reasons

    if pnl_pct is not None and trim_profit is not None and pnl_pct >= trim_profit and m.rsi_14 is not None and m.rsi_14 >= 75:
        reasons.append(f"浮盈 {pnl_pct:.1f}% 触及用户明确的止盈复核线，且RSI偏热")
        return "减仓候选", reasons

    if (
        market_signal == "风险偏好转弱/先控回撤"
        and m.ret_20d_pct is not None
        and m.ret_20d_pct < 0
        and m.rel_spy_20d_pct is not None
        and m.rel_spy_20d_pct < 0
    ):
        reasons.append("市场风险偏好转弱且个股20日弱于SPY")
        return "观望但提高警戒", reasons

    if (
        market_signal == "风险偏好偏强/顺风"
        and state == "趋势健康"
        and (m.rsi_14 is None or m.rsi_14 < 70)
        and (m.vol_30d_ann_pct is None or m.vol_30d_ann_pct <= 100)
    ):
        reasons.append("市场顺风且个股趋势健康，需再核验基本面/估值/新闻")
        return "现有持仓加仓候选", reasons

    reasons.append("未触发强动作条件")
    if market_signal:
        reasons.append(f"市场环境：{market_signal}")
    return "继续持有", reasons


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "无\n"
    lines = ["|" + "|".join(columns) + "|", "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in rows:
        lines.append("|" + "|".join(str(row.get(col, "")) for col in columns) + "|")
    return "\n".join(lines) + "\n"


def upsert_snapshot_log(
    holdings: pd.DataFrame, metrics: dict[str, QuoteMetrics], path: Path
) -> str:
    columns = [
        "snapshot_date",
        "ticker",
        "shares",
        "avg_cost",
        "last_price",
        "market_value",
        "cost_basis",
        "unrealized_pnl",
        "unrealized_pnl_pct",
    ]
    rows: list[dict[str, Any]] = []
    expected_tickers: list[str] = []
    for _, row in holdings.iterrows():
        ticker = clean_ticker(row.get("ticker"))
        if not ticker:
            continue
        expected_tickers.append(ticker)
        shares = safe_float(row.get("shares"), 0.0)
        if shares <= 0:
            return f"本次持仓市值快照跳过：{ticker} 股数待确认。"
        avg_cost = safe_float(row.get("avg_cost"), 0.0)
        metric = metrics.get(ticker)
        if metric is None or metric.close is None or not metric.price_as_of:
            return f"本次持仓市值快照跳过：{ticker} 缺少已完成日线价格。"
        last_price = metric.close
        market_value = shares * last_price
        cost_basis = shares * avg_cost if avg_cost else np.nan
        pnl = market_value - cost_basis if math.isfinite(cost_basis) else np.nan
        pnl_pct = (pnl / cost_basis * 100.0) if math.isfinite(cost_basis) and cost_basis else np.nan
        rows.append(
            {
                "snapshot_date": metric.price_as_of,
                "ticker": ticker,
                "shares": shares,
                "avg_cost": avg_cost,
                "last_price": last_price,
                "market_value": market_value,
                "cost_basis": cost_basis,
                "unrealized_pnl": pnl,
                "unrealized_pnl_pct": pnl_pct,
            }
        )
    if not rows or not expected_tickers:
        return "本次持仓市值快照跳过：没有可确认持仓。"
    snapshot_dates = {str(row["snapshot_date"]) for row in rows}
    if len(snapshot_dates) != 1:
        return "本次持仓市值快照跳过：持仓价格不属于同一完成交易日。"
    snapshot_date = snapshot_dates.pop()
    new_rows = pd.DataFrame(rows, columns=columns)
    if path.exists():
        existing = pd.read_csv(path)
        if "snapshot_date" in existing.columns:
            existing = existing[existing["snapshot_date"].astype(str) != snapshot_date]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    combined = combined[columns].sort_values(["snapshot_date", "ticker"])
    combined.to_csv(path, index=False)
    return f"本次持仓市值快照：已写入 {snapshot_date}，共 {len(rows)} 只持仓。"


def portfolio_snapshot_drawdown(path: Path) -> str:
    if not path.exists():
        return "持仓市值快照：暂无历史样本，无法计算样本回撤。"
    snapshots = pd.read_csv(path)
    required = {"snapshot_date", "ticker", "shares", "market_value"}
    if snapshots.empty or not required.issubset(snapshots.columns):
        return "持仓市值快照：样本格式不足，无法计算样本回撤。"
    compositions = snapshots.groupby("snapshot_date").apply(
        lambda frame: tuple(
            sorted(
                (clean_ticker(row["ticker"]), safe_float(row["shares"], 0.0))
                for _, row in frame.iterrows()
            )
        ),
        include_groups=False,
    )
    if len(set(compositions.tolist())) > 1:
        return "持仓市值快照：持仓标的或股数发生变化，且缺少现金流调整，停止计算跨期回撤。"
    daily = snapshots.groupby("snapshot_date", as_index=True)["market_value"].sum().sort_index()
    if len(daily) < 2:
        return "持仓市值快照：仅 1 个交易日样本，样本最大回撤待累计。"
    drawdown = daily / daily.cummax() - 1.0
    return (
        f"持仓市值快照：{len(daily)} 个交易日样本，"
        f"当前回撤 {pct(float(drawdown.iloc[-1] * 100.0))}，"
        f"样本内最大回撤 {pct(float(drawdown.min() * 100.0))}。该口径未调整买卖现金流，不等同于真实投资收益率。"
    )


def build_report(
    holdings: pd.DataFrame,
    watchlist: pd.DataFrame,
    opening_universe: pd.DataFrame,
    candidate_watchlist: pd.DataFrame,
    metrics: dict[str, QuoteMetrics],
    args: argparse.Namespace,
) -> str:
    local_now = datetime.now().astimezone()
    now_et = local_now.astimezone(ET)
    now = local_now.isoformat(timespec="minutes")
    holdings_rows: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    opening_rows: list[dict[str, Any]] = []
    persistent_candidate_rows: list[dict[str, Any]] = []
    watch_rows: list[dict[str, Any]] = []
    role_notes: list[str] = []
    action_rank = {
        "卖出审查": 4,
        "减仓候选": 3,
        "观望但提高警戒": 2,
        "现有持仓加仓候选": 1,
        "继续持有": 0,
    }
    market_signal, market_reasons = market_regime(metrics)
    heat_signal, heat_reasons = market_heat(metrics)
    market_rows = market_environment_rows(metrics)

    total_value = 0.0
    prepared = []
    for _, row in holdings.iterrows():
        ticker = clean_ticker(row.get("ticker"))
        if not ticker:
            continue
        shares = safe_float(row.get("shares"), 0.0)
        provided_mv = safe_float(row.get("market_value"), 0.0)
        metric = metrics.get(ticker)
        latest = metric.close if metric and metric.close is not None else safe_float(row.get("last_price"), 0.0)
        mv = provided_mv if provided_mv > 0 else shares * latest
        total_value += mv
        prepared.append((row, ticker, shares, latest, mv, metric))

    actions: list[tuple[int, str, str]] = []
    for row, ticker, shares, latest, mv, metric in prepared:
        metric = metric or empty_metrics(ticker, "missing")
        avg_cost = safe_float(row.get("avg_cost"), 0.0)
        provided_weight = safe_float(row.get("portfolio_weight_pct"), 0.0)
        weight = mv / total_value * 100.0 if total_value else provided_weight
        pnl_pct = ((latest / avg_cost - 1.0) * 100.0) if avg_cost and latest else None
        missing_position_value = mv <= 0 and provided_weight <= 0
        missing_cost = avg_cost <= 0
        action, reasons = action_for_holding(row, metric, pnl_pct, market_signal)
        actions.append((action_rank.get(action, 0), action, ticker))
        holdings_rows.append(
            {
                "Ticker": ticker,
                "价格": money(latest),
                "数据截止": metric.price_as_of or "待确认",
                "账户占比(事实)": "待确认" if missing_position_value else pct(weight),
                "浮盈亏": "待确认" if missing_cost else pct(pnl_pct),
                "1日": pct(metric.ret_1d_pct),
                "20日": pct(metric.ret_20d_pct),
                "相对SPY20日": pct(metric.rel_spy_20d_pct),
                "RSI": num(metric.rsi_14, 1),
                "状态": technical_state(metric),
                "动作底稿": action,
            }
        )
        risk_rows.append(
            {
                "Ticker": ticker,
                "成本回撤": "待确认" if missing_cost else pct(pnl_pct),
                "距1年最高收盘": pct(metric.current_drawdown_1y_pct),
                "60日最大回撤": pct(metric.max_drawdown_60d_pct),
                "1年最大回撤": pct(metric.max_drawdown_1y_pct),
                "30日年化波动": pct(metric.vol_30d_ann_pct),
                "95%单日VaR": pct(metric.var_95_1d_pct),
                "95%单日CVaR": pct(metric.cvar_95_1d_pct),
                "最差单日": pct(metric.worst_1d_pct),
            }
        )
        role_notes.append(
            f"- {ticker}: {action}。依据：{'; '.join(reasons)}。核心买入逻辑：{clean_text(row.get('core_thesis'))}。逻辑破坏线：{clean_text(row.get('thesis_break_rule'))}。"
        )

    for _, row in watchlist.iterrows():
        ticker = clean_ticker(row.get("ticker"))
        if not ticker:
            continue
        metric = metrics.get(ticker) or empty_metrics(ticker, "missing")
        score, reasons = candidate_score(metric)
        watch_rows.append(
            {
                "Ticker": ticker,
                "价格": money(metric.close),
                "数据截止": metric.price_as_of or "待确认",
                "20日": pct(metric.ret_20d_pct),
                "60日": pct(metric.ret_60d_pct),
                "相对SPY20日": pct(metric.rel_spy_20d_pct),
                "RSI": num(metric.rsi_14, 1),
                "状态": technical_state(metric),
                "候选分": score if score > -90 else "NA",
                "要点": "; ".join(reasons[:3]),
            }
        )
    watch_rows = sorted(watch_rows, key=lambda x: safe_float(x.get("候选分"), -99), reverse=True)
    opening_rows = build_opening_candidate_rows(opening_universe, metrics, {x[1] for x in prepared}, args.opening_candidate_limit)
    persistent_candidate_rows = build_persistent_candidate_rows(candidate_watchlist, metrics)

    if not holdings_rows:
        overall = "未发现真实持仓，今天只能生成开仓候选和模板检查。"
    elif actions:
        action_summary = "；".join(f"{ticker}「{action}」" for _, action, ticker in actions)
        overall = f"今日主结论：{action_summary}。市场环境为「{market_signal}」，大盘热度为「{heat_signal}」。"
    else:
        overall = f"今日主结论：未触发强动作条件，市场环境为「{market_signal}」，大盘热度为「{heat_signal}」，默认少动。"

    report = [
        "# 每日美股持仓分析底稿",
        "",
        f"- 生成时间：{now}",
        f"- 美东市场阶段：{market_phase(now_et)}；量化表默认只使用已完成日线，常规盘当日未完成 bar 不参与计算。",
        f"- 持仓文件：`{args.holdings}`",
        f"- 持仓分析视图：{getattr(args, 'holdings_source_note', '券商派生持仓')}。",
        f"- 截图快照日志：`{args.snapshot_log}`",
        f"- 开仓候选池：`{args.opening_universe}`",
        f"- 持续观察池：`{args.candidate_watchlist}`",
        f"- 说明：本报告是研究辅助，不是投资建议；所有新闻、财报和重大事件需在最终决策前复核来源。",
        "",
        "## 0. 今日结论",
        "",
        overall,
        "",
        "持仓数量、市值和仓位只作为事实记录，不作为买卖动作的默认触发器；买卖判断优先看市场环境、个股事实、趋势、估值预期和可验证风险。",
        "",
        "## 1. 市场环境",
        "",
        f"市场结论：{market_signal}。大盘热度：{heat_signal}。环境依据：{'; '.join(market_reasons) if market_reasons else '待确认'}。热度依据：{'; '.join(heat_reasons) if heat_reasons else '待确认'}。",
        "",
        markdown_table(market_rows, ["标的", "名称", "用途", "价格", "数据截止", "1日", "5日", "20日", "60日", "相对SPY20日", "RSI", "状态"]),
        "",
        "## 2. 持仓事实表",
        "",
        markdown_table(holdings_rows, ["Ticker", "价格", "数据截止", "账户占比(事实)", "浮盈亏", "1日", "20日", "相对SPY20日", "RSI", "状态", "动作底稿"]),
        "",
        "## 3. 量化回撤与尾部风险",
        "",
        "回撤指标基于历史日收益和可确认成本字段，不能预测未来；缺少股数、市值或仓位不影响买卖动作判断。",
        "",
        markdown_table(risk_rows, ["Ticker", "成本回撤", "距1年最高收盘", "60日最大回撤", "1年最大回撤", "30日年化波动", "95%单日VaR", "95%单日CVaR", "最差单日"]),
        "",
        getattr(args, "snapshot_status", "本次持仓市值快照状态：待确认。"),
        "",
        portfolio_snapshot_drawdown(Path(args.snapshot_log)),
        "",
        "## 4. 持续观察候选池",
        "",
        "`研究队列/持续观察/接近触发` 都不是买入建议；只有完成事实核验并升级为 `开仓候选/待确认` 后，才进入邮件机会提示。",
        "",
        markdown_table(persistent_candidate_rows, ["Ticker", "状态", "全维分", "覆盖率", "价格", "数据截止", "1日", "20日", "相对SPY20日", "技术预筛", "技术风险", "下个事件", "入场条件", "失效条件"]),
        "",
        "## 5. 量化发现队列",
        "",
        "以下最多 3 只仅用于发现和后续研究，不能直接写成开仓建议；必须先进入持久候选池并通过完整门禁。",
        "",
        markdown_table(opening_rows, ["Ticker", "公司", "主题", "价格", "数据截止", "20日", "60日", "相对SPY20日", "RSI", "候选分", "量化理由", "风险核验"]),
        "",
        "## 6. 用户指定观察池",
        "",
        "观察池仅补充你主动关注的 ticker，不等同于开仓建议。",
        "",
        markdown_table(watch_rows, ["Ticker", "价格", "数据截止", "20日", "60日", "相对SPY20日", "RSI", "状态", "候选分", "要点"]),
        "",
        "## 7. Subagent 审阅输入",
        "",
        "本节不是最终多角色结论，只是给自动化运行时实际派生 subagent 的事实输入和审阅问题。最终报告必须整合独立 subagent 输出。",
        "",
        "### 持仓事实核验员输入",
        "",
        "\n".join(role_notes) if role_notes else "无真实持仓，待填写。",
        "",
        "### 公司事实与SEC核验员输入",
        "",
        "subagent 必须核对公司公告、SEC 文件、财报日期、重大新闻、盘后/成交量/行业事实，并标注来源和日期；不可确认的内容写待确认。",
        "",
        "### 市场环境输入",
        "",
        f"市场结论：{market_signal}。大盘热度：{heat_signal}。核心依据：{'; '.join(market_reasons + heat_reasons) if market_reasons or heat_reasons else '待确认'}。subagent 必须判断大盘、科技/成长、小盘、市场广度、信用风险偏好、半导体、软件和 VIX 是否支持今天的动作。",
        "",
        "### 基本面与买入逻辑分析师输入",
        "",
        "subagent 必须基于当天可确认的 10-K/10-Q/8-K/6-K、财报、监管、诉讼、管理层或核心客户变化，判断原始买入逻辑是增强、未变、走弱、被破坏还是信息不足。",
        "",
        "### 估值与预期分析师输入",
        "",
        "subagent 必须判断估值、市场预期、财报前预期差和价格是否已经提前反映好消息；脚本底稿不把估值倍数作为机械交易信号。",
        "",
        "### 风控与反方辩手输入",
        "",
        "subagent 必须检查止损、回撤、波动、市场风险偏好、板块拥挤、财报前风险、一票否决项，并回答：如果这笔投资是错的，最可能错在哪里？今天是否出现支持反方观点的新证据？有没有比继续持有更保守的选择？持仓数量/仓位字段不得作为默认买卖理由。",
        "",
        "### 主 agent 仲裁规则",
        "",
        "市场环境、个股事实、估值预期和风控证据共同仲裁；触及止损/复核线、市场风险偏好恶化且个股走弱、或出现已核验经营性利空时，风控结论优先于收益想象。持仓数量、市值和仓位只记录，不作为动作触发器。",
        "",
        "## 8. 明日关注清单",
        "",
        "- 不要求手工补齐 CSV；用户聊天没有提供的持仓数量、市值、仓位字段继续写待确认，但不影响买卖动作判断。",
        "- 每天必须复核 SPY、QQQ、IWM、SMH/SOXX、IGV、RSP/SPY、HYG/IEF、VIX，判断大盘、科技、市场广度、信用风险偏好、半导体、软件和波动率是否支持动作。",
        "- 每日量化发现最多 3 只研究线索；只有持久候选池完成全维核验并发生状态升级时，才可写开仓候选/待确认。",
        "- 对任何现有持仓的加仓想法补充最新财报、SEC 文件、估值和新闻来源。",
        "- 若角色分歧大或证据不足，默认观望。",
        "",
    ]
    return "\n".join(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a daily US stock holding research brief.")
    parser.add_argument("--holdings", default=data_path("holdings_current.csv"))
    parser.add_argument("--analysis-overlay", default=data_path("holdings_analysis_overlay.csv"))
    parser.add_argument(
        "--analysis-overlay-audit",
        default=data_path("reports", "latest_holdings_analysis_overlay.json"),
    )
    parser.add_argument(
        "--analysis-overlay-manifest",
        default=data_path("holdings_analysis_overlay_manifest.json"),
    )
    parser.add_argument("--watchlist", default=data_path("watchlist_current.csv"))
    parser.add_argument("--opening-universe", default=config_path("opening_universe.csv"))
    parser.add_argument("--candidate-watchlist", default=data_path("candidate_watchlist.csv"))
    parser.add_argument("--candidate-state-log", default=data_path("candidate_state_log.jsonl"))
    parser.add_argument("--opening-candidate-limit", type=int, default=3)
    parser.add_argument("--period", default="1y")
    parser.add_argument("--output-dir", default=data_path("reports"))
    parser.add_argument("--snapshot-log", default=data_path("portfolio_snapshots.csv"))
    parser.add_argument("--commit-manifest", default=data_path("holdings_commit_manifest.json"))
    parser.add_argument("--skip-commit-verify", action="store_true")
    parser.add_argument("--skip-snapshot-log", action="store_true")
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "Run the full analysis on a synthetic portfolio with deterministic "
            "offline prices. No network, no private data, no configuration."
        ),
    )
    args = parser.parse_args()

    if args.demo:
        args.skip_commit_verify = True
        args.skip_snapshot_log = True
        if args.holdings == data_path("holdings_current.csv"):
            args.holdings = str(Path(__file__).resolve().parent / EXAMPLE_DEMO_HOLDINGS)
        # Point the optional inputs at a demo-only directory so a bootstrapped
        # private watchlist can never leak into a demo run.
        demo_inputs = Path(args.output_dir) / "demo"
        for unset in ("analysis_overlay", "watchlist", "candidate_watchlist"):
            setattr(args, unset, str(demo_inputs / f"{unset}.csv"))

    logical_holdings_path = Path(args.holdings)
    holdings_path = logical_holdings_path
    analysis_overlay_path = Path(args.analysis_overlay)
    analysis_overlay_audit_path = Path(args.analysis_overlay_audit)
    analysis_overlay_manifest_path = Path(args.analysis_overlay_manifest)
    watchlist_path = Path(args.watchlist)
    opening_universe_path = Path(args.opening_universe)
    candidate_watchlist_path = Path(args.candidate_watchlist)
    candidate_state_log_path = Path(args.candidate_state_log)
    snapshot_log_path = Path(args.snapshot_log)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_commit_verify:
        try:
            broker_manifest = verify_commit_manifest(
                Path(args.commit_manifest), logical_holdings_path
            )
            holdings_path = resolve_committed_path(
                broker_manifest, "holdings", logical_holdings_path
            )
            broker_events_path = resolve_committed_path(
                broker_manifest, "broker_events", Path(data_path("broker_events.csv"))
            )
        except (OSError, json.JSONDecodeError, ReconciliationError) as exc:
            raise SystemExit(f"holdings commit verification failed: {exc}") from exc
    else:
        broker_manifest = None
        broker_events_path = Path(data_path("broker_events.csv"))

    if not args.demo:
        # A demo run has no candidate ledger to reconcile against, so there is
        # nothing for this gate to verify.
        try:
            validate_candidate_watchlist(candidate_state_log_path, candidate_watchlist_path)
        except (OSError, CandidateEventError) as exc:
            raise SystemExit(f"candidate state verification failed: {exc}") from exc

    overlay_files = (
        analysis_overlay_path,
        analysis_overlay_audit_path,
        analysis_overlay_manifest_path,
    )
    present_overlay_files = [path for path in overlay_files if path.exists()]
    if present_overlay_files and len(present_overlay_files) != len(overlay_files):
        raise SystemExit("analysis overlay verification failed: overlay files are incomplete")

    holdings = read_table(holdings_path)
    if args.demo:
        args.holdings_source_note = (
            f"DEMO：合成组合 `{EXAMPLE_DEMO_HOLDINGS}` 与本地确定性行情，非真实持仓与行情"
        )
    else:
        args.holdings_source_note = f"券商派生持仓 `{logical_holdings_path}`"
    if present_overlay_files:
        try:
            overlay_audit = verify_overlay(
                analysis_overlay_manifest_path,
                analysis_overlay_path,
                analysis_overlay_audit_path,
            )
        except (OSError, OverlayError) as exc:
            raise SystemExit(f"analysis overlay verification failed: {exc}") from exc
        if overlay_audit.get("overlay_status") == "ACTIVE":
            try:
                later_events = later_position_event_ids(
                    broker_events_path, overlay_audit.get("corrected_at")
                )
            except OverlayError as exc:
                raise SystemExit(f"analysis overlay freshness check failed: {exc}") from exc
            if later_events:
                sample = ", ".join(later_events[:3])
                raise SystemExit(
                    "analysis overlay is stale after verified broker position events: "
                    f"{sample}; refresh or clear the chat correction"
                )
            holdings = read_table(analysis_overlay_path)
            if holdings.empty:
                raise SystemExit(
                    "analysis overlay confirms an empty holding set; no holdings ticker to analyze"
                )
            args.holdings_source_note = (
                f"用户聊天 analysis overlay，correction_id={overlay_audit.get('correction_id')}，"
                f"corrected_at={overlay_audit.get('corrected_at')}；未提供字段不从券商或旧持仓继承"
            )
    watchlist = read_table(watchlist_path)
    opening_universe = read_opening_universe(opening_universe_path)
    candidate_watchlist = read_table(candidate_watchlist_path)
    if not holdings.empty and "ticker" in holdings.columns:
        holdings["ticker"] = holdings["ticker"].map(clean_ticker)
        holdings = holdings[holdings["ticker"] != ""]
    if not watchlist.empty and "ticker" in watchlist.columns:
        watchlist["ticker"] = watchlist["ticker"].map(clean_ticker)
        watchlist = watchlist[watchlist["ticker"] != ""]

    tickers = []
    if not holdings.empty and "ticker" in holdings.columns:
        tickers.extend(holdings["ticker"].tolist())
    if not watchlist.empty and "ticker" in watchlist.columns:
        tickers.extend(watchlist["ticker"].tolist())
    if not opening_universe.empty and "ticker" in opening_universe.columns:
        tickers.extend(opening_universe["ticker"].tolist())
    if not candidate_watchlist.empty and "ticker" in candidate_watchlist.columns:
        tickers.extend(candidate_watchlist["ticker"].map(clean_ticker).tolist())
    tickers.extend(BENCHMARKS)
    tickers = sorted({t for t in tickers if t})

    frames = demo_price_frames(tickers) if args.demo else download_prices(tickers, args.period)
    spy_close = frames.get("SPY", pd.DataFrame()).get("Close") if "SPY" in frames else None
    metrics = {ticker: build_metrics(ticker, frame, spy_close) for ticker, frame in frames.items()}

    if not args.skip_snapshot_log:
        args.snapshot_status = upsert_snapshot_log(holdings, metrics, snapshot_log_path)
    else:
        args.snapshot_status = "本次持仓市值快照：按命令跳过。"

    report = build_report(
        holdings, watchlist, opening_universe, candidate_watchlist, metrics, args
    )
    if args.demo:
        report = DEMO_BANNER + report
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = output_dir / f"portfolio_brief_{stamp}.md"
    output_path.write_text(report, encoding="utf-8")
    if args.demo:
        print(report)
        print(f"\n---\nFull brief written to {output_path}")
    else:
        print(output_path)


if __name__ == "__main__":
    main()
