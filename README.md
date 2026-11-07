# Builderr trading agent – Rebound-Aware Volatility-Targeted Momentum (RAVM)

| | |
|---|---|
| Builder | Avidha Chandra |
| Agent file | `agent.py` – `decide(market_state, portfolio_state, cash)` |
| Dependencies | none – Python standard library only; no network, no LLM, no API keys |
| Decision time | about 1 ms per call |
| Check | `python preview.py agent.py` → all four safety checks PASS |

## Run it

```
python preview.py agent.py                      # Builderr's admission preview on the 3 sample windows
python backtest.py agent.py vol_spike_snapback  # day-by-day equity, regime and weights for one window
```

## Files

| File | Purpose |
|---|---|
| `agent.py` | **the agent** – `decide()` |
| `preview.py`, `sample_regimes.json.gz`, `universe.json` | from Builderr's starter template, unchanged (MIT, see `LICENSE-builderr-template`) |
| `drawdown_momentum.py` | Builderr's house reference bot, unchanged, for comparison |
| `backtest.py` | a day-by-day trace of the same fills as `preview.py` (next open, 5/10 bps slippage) |

## Strategy

The agent reads only the daily bars it is given and switches between four books, using QQQ (SPY as fallback):

| Regime | When | Book |
|---|---|---|
| **TREND** | QQQ above a rising 50-day average | top 5 of QQQ/SPY + 15 liquid tech and growth names by 60/20-day momentum, inverse-volatility weighted, plus a 12–20% TQQQ sleeve when volatility is moderate |
| **REBOUND** | QQQ ≥ 7% below its 60-day high and stabilising (an up day that is the second in a row or closes above the 5-day average) | the 4 highest-beta names plus QQQ/SPY, with a 10–16% TQQQ sleeve |
| **DEFENCE / CASH** | crash brake (QQQ day ≤ −2.5% or 5-day fall ≤ −4% while holding a trend book), a failed rebound (QQQ 5% below the entry close → 5 days in cash), or a down-trend that has not stabilised | cash |

After a brake, going back to TREND also needs a close above the 20-day average and within 1% of the 10-day high,
which stops whipsaw re-entries.

## Risk limits (inside the published caps)

- each target weight ≤ 24%, trimmed above 27% (cap 30%)
- beta-adjusted gross scaled to ≤ 1.40× (cap 1.5×; TQQQ counts 3×)
- long only, at most 45 orders a day (cap 50), sells sized before buys, buys scaled to the cash available
- rebalances only on a regime change, a weight drift above 5 points, or a cap breach – no daily churn
- any internal error returns no orders for that day instead of crashing

## Sample windows (`python preview.py agent.py`)

| Window | RAVM | MaxDD | House reference (`drawdown_momentum.py`) |
|---|---|---|---|
| calm_uptrend (2021 Jun–Aug) | **+20.71%** | 4.07% | +8.22% |
| moderate_selloff (2021 Sep–Oct) | −5.93% | 6.79% | −5.77% |
| vol_spike_snapback (2020 Feb–Apr) | **−0.91%** | 22.46% | −7.52% |

Peak gross 1.41×, peak single-name weight 24%, worst drawdown 22.5% (blow-up limit 50%).

**Limits, stated plainly:** the brake and rebound thresholds were chosen by looking at these same three windows,
so forward results will likely be weaker than the table. The rebound book is deliberately aggressive: it is the
part of the strategy that has to earn the return after a sell-off, and its 5% stop is what caps the cost when a
rebound fails.
