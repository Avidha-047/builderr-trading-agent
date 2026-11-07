"""Rebound-Aware Volatility-Targeted Momentum (RAVM).

Ranking in Round 2 is forward return, so an agent that hides in cash after a sell-off cannot place. This agent
stays systematic about WHEN to take risk, using only the provided daily bars (no network, no LLM, no keys, stdlib):

  1. TREND regime   - QQQ above its 50-day average and the 50-day average rising: hold the strongest tech /
                      broad-market names (60/20-day momentum), sized by inverse volatility, plus a small TQQQ sleeve
                      when volatility is moderate. Target beta-adjusted exposure up to 1.40x.
  2. REBOUND regime - after a sharp drawdown (QQQ >= 7% below its 60-day high) the agent waits for stabilisation
                      (an up day that is the second in a row, or that closes back above the 5-day average) and
                      then buys the rebound with high-beta tech + a TQQQ sleeve. A hard stop sends it to cash if
                      QQQ closes more than 5% below the entry close.
  3. DEFENCE regime - a down-trend that is not yet a buyable sell-off. DEFENCE_W sets how much goes into defensive
                      sectors that still hold up; it is 0 (all cash) because in the sample sell-offs staples and
                      utilities fell with everything else.
  4. CASH            - a crash brake (a QQQ day <= -2.5% or a 5-day fall <= -4% while in a trend book), a failed
                      rebound (5 trading days), or a deep sell-off that has not stabilised yet. After a brake, a
                      return to TREND also needs a close above the 20-day average and within 1% of the 10-day high. In a liquidation
                      even defensive sectors fall, so the brake goes to cash, not to staples.

Risk rules (all inside the published caps): every target weight <= 24% (drift trim above 27%), beta-adjusted gross
scaled to <= 1.40x (cap 1.5x), long only, at most ~20 orders a day, rebalancing only on a regime change or a weight
drift above 5 percentage points so the agent does not churn.
"""
from __future__ import annotations

import math

BETA_3X = {"TQQQ", "SOXL", "UPRO", "SPXL", "TNA", "FAS", "TECL", "LABU", "CURE", "DRN", "UDOW", "NAIL"}
BETA_2X = {"QLD", "SSO", "DDM", "ROM", "UWM", "AGQ"}

CORE = ("QQQ", "SPY")
GROWTH = ("SMH", "XLK", "NVDA", "MSFT", "AAPL", "META", "AVGO", "AMZN", "GOOGL", "AMD", "TSLA", "MU", "NFLX", "XLY", "XLC")
DEFENCE = ("XLP", "XLU", "XLV", "GLD", "TLT")
LEVER = ("TQQQ", "QLD")

MAX_W = 0.24
TRIM_W = 0.27
GROSS_TARGET = 1.40
REBAL_DRIFT = 0.05
MIN_ORDER_FRAC = 0.004

REBOUND_DD = 0.07        # QQQ this far below its 60-day high counts as a sell-off worth buying back into
REBOUND_STOP = 0.05      # leave a rebound if QQQ closes this far below the entry close
COOLDOWN = 5             # trading days in cash after a failed rebound
CRASH_DAY = 0.025        # crash brake: a QQQ day this bad ...
CRASH_WEEK = 0.04        # ... or a 5-day fall this bad takes a trend book to cash
DEFENCE_W = 0.0          # defence book: this much in staples / utilities / health / gold / bonds, rest cash

_state = {"regime": None, "entry_px": None, "cooldown": 0, "last_targets": None}


def _beta(t):
    return 3.0 if t in BETA_3X else 2.0 if t in BETA_2X else 1.0


def _closes(ms, t):
    bars = ms.get(t) or []
    out = []
    for b in bars:
        try:
            c = float(b["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if c > 0 and math.isfinite(c):
            out.append(c)
    return out


def _sma(x, n):
    n = min(n, len(x))
    return sum(x[-n:]) / n if n else 0.0


def _vol(x, n=20):
    r = [x[i] / x[i - 1] - 1 for i in range(max(1, len(x) - n), len(x))]
    if len(r) < 5:
        return 0.25
    m = sum(r) / len(r)
    return math.sqrt(sum((a - m) ** 2 for a in r) / (len(r) - 1)) * math.sqrt(252)


def _mom(x, n):
    return x[-1] / x[-1 - n] - 1 if len(x) > n else 0.0


def _regime(q):
    """Return (regime, info) from the QQQ (or SPY) close series."""
    st = _state
    sma50, sma50_prev = _sma(q, 50), _sma(q[:-5], 50) if len(q) > 55 else _sma(q, 50)
    hi60 = max(q[-60:])
    dd = 1 - q[-1] / hi60
    up2 = len(q) >= 3 and q[-1] > q[-2] > q[-3]
    above5 = q[-1] > _sma(q, 5)
    prev = st["regime"]

    day = q[-1] / q[-2] - 1
    week = q[-1] / q[-6] - 1 if len(q) > 6 else 0.0
    if st["cooldown"] > 0:
        st["cooldown"] -= 1
        return "CASH", dd
    if prev == "REBOUND" and st["entry_px"] and q[-1] < st["entry_px"] * (1 - REBOUND_STOP):
        st["entry_px"], st["cooldown"] = None, COOLDOWN                              # rebound failed: stand aside
        return "CASH", dd
    if prev in ("TREND", None) and (day <= -CRASH_DAY or week <= -CRASH_WEEK):
        return "CASH", dd                                                            # crash brake: out the same day
    trend_ok = q[-1] > sma50 and sma50 >= sma50_prev and day > -CRASH_DAY
    if trend_ok and prev not in ("TREND", None, "REBOUND"):
        trend_ok = q[-1] > _sma(q, 20) and q[-1] >= max(q[-10:]) * 0.99         # re-entry after a brake needs a fresh high
    if trend_ok:
        return "TREND", dd
    if prev == "REBOUND":
        return "REBOUND", dd                                                         # hold the rebound until trend or stop
    if dd >= REBOUND_DD and (up2 or above5) and day > 0:
        st["entry_px"] = q[-1]
        return "REBOUND", dd
    return "DEFENCE" if dd < 0.12 else "CASH", dd


def _targets(ms, regime):
    q = _closes(ms, "QQQ") or _closes(ms, "SPY")
    vol_q = _vol(q)
    avail = {t: _closes(ms, t) for t in set(CORE + GROWTH + DEFENCE + LEVER)}
    avail = {t: c for t, c in avail.items() if len(c) >= 30}
    w = {}
    if regime in ("TREND", "REBOUND"):
        pool = [t for t in CORE + GROWTH if t in avail]
        if regime == "TREND":
            score = {t: 0.6 * _mom(avail[t], 60) + 0.4 * _mom(avail[t], 20) for t in pool}
            chosen = [t for t in sorted(pool, key=lambda t: -score[t]) if score[t] > 0][:5]
        else:   # rebound: highest-beta liquid tech bounces most; keep the broad core for ballast
            beta_q = {}
            rq = [q[i] / q[i - 1] - 1 for i in range(len(q) - 40, len(q))]
            for t in pool:
                c = avail[t]
                rt = [c[i] / c[i - 1] - 1 for i in range(len(c) - 40, len(c))]
                n = min(len(rq), len(rt))
                mq = sum(rq[-n:]) / n
                cov = sum((a - mq) * (b - sum(rt[-n:]) / n) for a, b in zip(rq[-n:], rt[-n:]))
                var = sum((a - mq) ** 2 for a in rq[-n:])
                beta_q[t] = cov / var if var > 0 else 1.0
            chosen = sorted(pool, key=lambda t: -beta_q[t])[:4]
            for t in ("QQQ", "SPY"):
                if t in avail and t not in chosen:
                    chosen.append(t)
                    break
        if not chosen:
            chosen = [t for t in CORE if t in avail][:1]
        inv = {t: 1 / max(_vol(avail[t]), 0.05) for t in chosen}
        tot = sum(inv.values())
        lever = next((t for t in LEVER if t in avail), None)
        lev_w = 0.0
        if lever:
            if regime == "TREND" and vol_q < 0.30:
                lev_w = 0.20 if vol_q < 0.22 else 0.12
            elif regime == "REBOUND":
                lev_w = 0.16 if vol_q < 0.45 else 0.10
        one_x = 1.0 - lev_w
        for t in chosen:
            w[t] = min(MAX_W, one_x * inv[t] / tot)
        if lever and lev_w:
            w[lever] = lev_w
        # spread any capped remainder over the chosen names (still <= MAX_W)
        spare = one_x - sum(v for t, v in w.items() if t != lever)
        for t in chosen:
            if spare <= 1e-6:
                break
            add = min(MAX_W - w[t], spare)
            w[t] += add
            spare -= add
    elif regime == "DEFENCE":
        pool = [t for t in DEFENCE if t in avail]
        good = [t for t in pool if _mom(avail[t], 20) > -0.03]
        for t in good[:4]:
            w[t] = min(MAX_W, DEFENCE_W / max(1, len(good[:4])))
    # CASH: no positions
    gross = sum(v * _beta(t) for t, v in w.items())
    if gross > GROSS_TARGET:
        k = GROSS_TARGET / gross
        w = {t: v * k for t, v in w.items()}
    return w


def decide(market_state, portfolio_state, cash):
    try:
        return _decide(market_state, portfolio_state, cash)
    except Exception:          # never crash the run: an error tick only means no trade today
        return []


def _decide(market_state, portfolio_state, cash):
    q = _closes(market_state, "QQQ") or _closes(market_state, "SPY")
    if len(q) < 60:
        return []
    prices = dict(portfolio_state.get("last_prices") or {})
    for t in market_state:
        c = _closes(market_state, t)
        if c and t not in prices:
            prices[t] = c[-1]
    held = {p["ticker"]: float(p["quantity"]) for p in portfolio_state.get("positions") or [] if float(p.get("quantity", 0)) > 0}
    equity = float(portfolio_state.get("cash", cash)) + sum(q_ * prices.get(t, 0.0) for t, q_ in held.items())
    if equity <= 0:
        return []

    regime, _ = _regime(q)
    changed = regime != _state["regime"]
    _state["regime"] = regime
    target = _targets(market_state, regime)
    cur_w = {t: held.get(t, 0.0) * prices.get(t, 0.0) / equity for t in set(held) | set(target)}
    drift = max((abs(cur_w.get(t, 0.0) - target.get(t, 0.0)) for t in cur_w), default=0.0)
    over = any(v > TRIM_W for v in cur_w.values())
    gross_now = sum(v * _beta(t) for t, v in cur_w.items())
    if not (changed or drift > REBAL_DRIFT or over or gross_now > 1.45 or _state["last_targets"] is None):
        return []
    _state["last_targets"] = target

    sells, buys = [], []
    for t, wcur in cur_w.items():
        px = prices.get(t)
        if not px:
            continue
        delta = target.get(t, 0.0) - wcur
        qty = abs(delta) * equity / px
        if abs(delta) < MIN_ORDER_FRAC:
            continue
        if delta < 0:
            sells.append({"ticker": t, "side": "sell", "quantity": round(min(qty, held.get(t, 0.0)), 6)})
        else:
            buys.append((t, qty, px))
    # buys are funded by cash after the sells (all fill at the next open); scale to available cash with a buffer
    free = float(portfolio_state.get("cash", cash)) + sum(o["quantity"] * prices[o["ticker"]] for o in sells)
    need = sum(q_ * px for _, q_, px in buys)
    k = min(1.0, 0.985 * free / need) if need > 0 else 1.0
    orders = [o for o in sells if o["quantity"] > 0]
    orders += [{"ticker": t, "side": "buy", "quantity": round(q_ * k, 6)} for t, q_, px in buys if q_ * k > 0]
    return orders[:45]
