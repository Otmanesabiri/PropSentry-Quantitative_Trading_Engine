"""
scripts/run_monte_carlo.py
===========================
Monte Carlo permutation simulator for drawdown risk estimation.

Permutes historical trade P&L sequences N times to estimate the
probability distribution of:
  - Maximum drawdown
  - Probability of hitting the 5% daily limit
  - Probability of hitting the 10% overall limit

Usage:
    python scripts/run_monte_carlo.py --trades results/bt.csv --simulations 10000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))


def run_simulation(
    pnl_series: list[float],
    initial_balance: float,
    n_simulations: int,
) -> dict:
    """
    Permute PnL series N times and compute drawdown statistics.

    Returns dict with:
        max_dd_mean, max_dd_p95, prob_5pct_limit, prob_10pct_limit
    """
    arr = np.array(pnl_series)
    max_dds: list[float] = []
    hit_5pct: int = 0
    hit_10pct: int = 0

    for _ in range(n_simulations):
        permuted = np.random.permutation(arr)
        equity = initial_balance + np.cumsum(permuted)
        equity = np.insert(equity, 0, initial_balance)
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / initial_balance
        max_dd = float(np.max(drawdown))
        max_dds.append(max_dd)

        if max_dd >= 0.05:
            hit_5pct += 1
        if max_dd >= 0.10:
            hit_10pct += 1

    max_dds_arr = np.array(max_dds)
    return {
        "n_simulations": n_simulations,
        "n_trades": len(pnl_series),
        "max_dd_mean": float(np.mean(max_dds_arr)),
        "max_dd_median": float(np.median(max_dds_arr)),
        "max_dd_p95": float(np.percentile(max_dds_arr, 95)),
        "max_dd_p99": float(np.percentile(max_dds_arr, 99)),
        "prob_5pct_limit": hit_5pct / n_simulations,
        "prob_10pct_limit": hit_10pct / n_simulations,
    }


def main() -> None:
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="PropSentry Monte Carlo Simulator")
    parser.add_argument("--trades", required=True, help="CSV file with pnl_usd column (from run_backtest.py)")
    parser.add_argument("--simulations", type=int, default=10_000, help="Number of permutations (default: 10,000)")
    parser.add_argument("--balance", type=float, default=5000.0, help="Initial balance (default: 5000)")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stdout, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
               colorize=True)

    path = Path(args.trades)
    if not path.exists():
        logger.error(f"File not found: {path}")
        logger.info("Run `python scripts/run_backtest.py --export results/bt.csv` first.")
        sys.exit(1)

    df = pd.read_csv(path)
    if "pnl_usd" not in df.columns:
        logger.error("CSV must contain a 'pnl_usd' column.")
        sys.exit(1)

    pnl_series = df["pnl_usd"].dropna().tolist()
    if len(pnl_series) < 10:
        logger.warning(f"Only {len(pnl_series)} trades — results may not be statistically significant.")

    logger.info(f"[MC] Running {args.simulations:,} simulations on {len(pnl_series)} trades...")
    stats = run_simulation(pnl_series, args.balance, args.simulations)

    print("\n" + "═" * 50)
    print("  Monte Carlo Risk Analysis")
    print("═" * 50)
    print(f"  Simulations     : {stats['n_simulations']:,}")
    print(f"  Trades sampled  : {stats['n_trades']}")
    print(f"  Max DD (mean)   : {stats['max_dd_mean']:.2%}")
    print(f"  Max DD (median) : {stats['max_dd_median']:.2%}")
    print(f"  Max DD (p95)    : {stats['max_dd_p95']:.2%}")
    print(f"  Max DD (p99)    : {stats['max_dd_p99']:.2%}")
    print(f"  P(hit 5% limit) : {stats['prob_5pct_limit']:.2%}")
    print(f"  P(hit 10% limit): {stats['prob_10pct_limit']:.2%}")
    print("═" * 50 + "\n")


if __name__ == "__main__":
    main()
