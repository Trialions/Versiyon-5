# optimizer.py — Adaptif parametre optimizasyon motoru
import csv
import os
from collections import defaultdict
from logger import log_info

TRADE_LOG = "data/trade_logs.csv"


def _read_closed_trades(log_path: str) -> list:
    if not os.path.exists(log_path):
        return []
    trades = []
    try:
        with open(log_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter=";"):
                note = row.get("Not", "")
                if not note or "OPEN" in note:
                    continue
                try:
                    pnl = float(row.get("KarUSD", 0) or 0)
                except ValueError:
                    pnl = 0.0
                trades.append({
                    "symbol": row.get("Sembol", ""),
                    "side":   row.get("Yon", ""),
                    "pnl":    pnl,
                    "reason": note,
                    "date":   row.get("Tarih", ""),
                })
    except Exception as e:
        log_info(f"Optimizer okuma hatası: {e}")
    return trades


def run_optimization(log_path: str = TRADE_LOG) -> str:
    """
    Son trade logunu analiz eder, parametre önerisi üretir.
    Dönüş: log paneline yazdırılacak mesaj.
    """
    trades = _read_closed_trades(log_path)
    if len(trades) < 5:
        return "[OPT] Yeterli işlem verisi yok (min. 5), optimizasyon atlandı."

    total    = len(trades)
    wins     = [t for t in trades if t["pnl"] > 0]
    losses   = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / total * 100
    avg_win  = sum(t["pnl"] for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
    rr       = abs(avg_win / avg_loss) if avg_loss else float("inf")
    sl_rate  = sum(1 for t in trades if "SL" in t["reason"]) / total * 100
    net_pnl  = sum(t["pnl"] for t in trades)

    suggestions = []

    # Kural 1: Kazanma oranı çok düşük → eşikleri sıkılaştır
    if win_rate < 35:
        suggestions.append("score_long_open  → +3  (daha seçici LONG filtresi)")
        suggestions.append("score_short_open → -3  (daha seçici SHORT filtresi)")

    # Kural 2: SL çok sık tetikleniyor → stop'u genişlet
    if sl_rate > 65:
        suggestions.append(f"hard_stop_pct    → +0.3  (SL oranı yüksek: %{sl_rate:.0f})")

    # Kural 3: Risk/Ödül düşük → take profit'i yükselt
    if 0 < rr < 1.2 and len(wins) >= 3:
        suggestions.append(f"take_profit_min_pct → +0.5  (R/R düşük: {rr:.2f}x)")

    # Kural 4: Net zarar + düşük win rate → trailing adımı küçült
    if net_pnl < 0 and win_rate < 40:
        suggestions.append("trailing_step_pct → -0.2  (kârı daha erken kilitlemek için)")

    header = (f"[OPT] {total} işlem analizi — "
              f"WR=%{win_rate:.0f} | R/R={rr:.2f}x | "
              f"SL=%{sl_rate:.0f} | PnL=${net_pnl:+.2f}")

    if not suggestions:
        return header + "\n[OPT] Mevcut parametreler yeterli, değişiklik önerilmedi."

    lines = [header] + [f"  → {s}" for s in suggestions]
    log_info("Optimizasyon önerileri oluşturuldu")
    return "\n".join(lines)


def compute_hourly_stats(log_path: str = TRADE_LOG) -> list:
    """Saatlik kazanma oranı ve PnL istatistikleri."""
    trades = _read_closed_trades(log_path)
    hours  = defaultdict(lambda: {"wins": 0, "total": 0, "pnl": 0.0})
    for t in trades:
        date_str = t.get("date", "")
        try:
            hour = int(date_str[11:13])
        except (IndexError, ValueError):
            continue
        hours[hour]["total"] += 1
        hours[hour]["pnl"]   += t["pnl"]
        if t["pnl"] > 0:
            hours[hour]["wins"] += 1

    result = []
    for h in sorted(hours):
        d  = hours[h]
        wr = d["wins"] / d["total"] * 100 if d["total"] else 0
        result.append({"hour": h, "win_rate": round(wr),
                        "pnl": round(d["pnl"], 1), "count": d["total"]})
    return result


def compute_coin_stats(log_path: str = TRADE_LOG) -> list:
    """Coin bazlı kazanma oranı ve PnL istatistikleri."""
    trades = _read_closed_trades(log_path)
    coins  = defaultdict(lambda: {"wins": 0, "total": 0, "pnl": 0.0})
    for t in trades:
        sym = t["symbol"]
        if not sym:
            continue
        coins[sym]["total"] += 1
        coins[sym]["pnl"]   += t["pnl"]
        if t["pnl"] > 0:
            coins[sym]["wins"] += 1

    result = []
    for sym, d in coins.items():
        wr = d["wins"] / d["total"] * 100 if d["total"] else 0
        result.append({"symbol": sym, "win_rate": round(wr),
                        "pnl": round(d["pnl"], 1)})
    result.sort(key=lambda x: -x["pnl"])
    return result
