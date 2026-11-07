"""Fast backtester mirroring preview.py's fills (next open, 5/10 bps slippage, cash-limited buys)."""
import gzip, json, importlib.util, sys
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import preview

def load(path):
    spec = importlib.util.spec_from_file_location("agent_" + str(abs(hash(path))), path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def run(agent_path, reg, verbose=False, params=None):
    A = load(agent_path)
    if params:
        for k, v in params.items(): setattr(A, k, v)
    bars = {t: preview._expand(rows) for t, rows in reg["bars"].items()}
    idx = {t: {b["ts"]: b for b in rows} for t, rows in bars.items()}
    dates = sorted({b["ts"] for rows in bars.values() for b in rows})
    ev = [d for d in dates if reg["eval_start"] <= d <= reg["eval_end"]]
    cash, pos, pending, curve, log = 100000.0, {}, [], [], []
    for d in ev:
        for o in pending:
            b = idx[o["ticker"]].get(d)
            if not b: continue
            slip = 0.001 if preview.beta(o["ticker"]) > 1 else 0.0005
            if o["side"] == "buy":
                px = b["open"] * (1 + slip); qty = min(o["quantity"], cash / px)
                if qty > 0: pos[o["ticker"]] = pos.get(o["ticker"], 0) + qty; cash -= qty * px
            else:
                qty = min(o["quantity"], pos.get(o["ticker"], 0))
                if qty > 0: pos[o["ticker"]] -= qty; cash += qty * b["open"] * (1 - slip)
        pending = []
        prices = {t: idx[t][d]["close"] for t in bars if d in idx[t]}
        eq = cash + sum(q * prices.get(t, 0) for t, q in pos.items())
        curve.append(eq)
        ms = {t: [b for b in bars[t] if b["ts"] <= d] for t in bars}
        ps = {"cash": cash, "positions": [{"ticker": t, "quantity": q, "avg_cost": 0} for t, q in pos.items() if q > 1e-9], "last_prices": prices}
        orders = A.decide(ms, ps, cash) or []
        pending = [o for o in orders if o["ticker"] in bars]
        if verbose:
            w = {t: round(q * prices.get(t, 0) / eq, 2) for t, q in pos.items() if q * prices.get(t, 0) / eq > 0.01}
            log.append((d, round(eq), getattr(A, "_state", {}).get("regime"), w))
    ret = curve[-1] / 100000 - 1
    return ret, preview._max_drawdown(curve), curve, log

if __name__ == "__main__":
    regs = json.loads(gzip.open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), 'sample_regimes.json.gz')).read())
    name = sys.argv[2]
    ret, mdd, curve, log = run(sys.argv[1], regs[name], verbose=True)
    for row in log: print(*row)
    print(f"ret {ret*100:.2f}% mdd {mdd*100:.2f}%")
