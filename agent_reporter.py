# agent_reporter.py — İşlem log analizi ve rapor üretici
import csv
import os
import ast
from datetime import datetime
from collections import Counter, defaultdict


def _read_trades(trade_log_csv: str):
    """CSV'yi okur, açık+kapanış eşleştirmesi yaparak kapalı işlem listesi döner."""
    if not os.path.exists(trade_log_csv):
        return None, "İşlem kaydı bulunamadı."

    closed, open_t = [], {}
    try:
        with open(trade_log_csv, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter=";"):
                note   = row.get("Not", "")
                symbol = row.get("Sembol", "")

                if "OPEN" in note:
                    try:
                        score_details = ast.literal_eval(note.split("=", 1)[1])
                    except Exception:
                        score_details = {"final_score": 0, "components": {}}
                    open_t[symbol] = {"details": score_details, "row": row}

                elif note and symbol in open_t:
                    entry = open_t.pop(symbol)
                    try:
                        pnl = float(row.get("KarUSD", 0) or 0)
                    except ValueError:
                        pnl = 0.0
                    closed.append({
                        "symbol":   symbol,
                        "side":     entry["row"].get("Yon", ""),
                        "details":  entry["details"],
                        "pnl_usd":  pnl,
                        "reason":   note,
                    })
    except Exception as e:
        return None, f"CSV okuma hatası: {e}"

    return (closed, "OK") if closed else (None, "Hiç kapalı işlem yok.")


# ──────────────────────────────────────────────
# Günlük Özet (Agent otomatik çağırır)
# ──────────────────────────────────────────────
def generate_daily_summary_report(trade_log_csv: str,
                                   agent_report_csv: str) -> str:
    trades, msg = _read_trades(trade_log_csv)
    if not trades:
        return msg

    total     = len(trades)
    wins      = sum(1 for t in trades if t["pnl_usd"] > 0)
    net_pnl   = sum(t["pnl_usd"] for t in trades)
    win_rate  = wins / total * 100 if total else 0.0

    report_dir = os.path.dirname(agent_report_csv)
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)

    is_new = not os.path.exists(agent_report_csv)
    with open(agent_report_csv, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if is_new:
            w.writerow(["Tarih","ToplamIslem","Kazanan","Kaybeden",
                        "KazanmaOrani","NetKarUSD"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M"),
                    total, wins, total - wins,
                    f"{win_rate:.1f}%", f"{net_pnl:+.2f}"])

    return (f"Rapor kaydedildi — {total} işlem | "
            f"Kazanma: %{win_rate:.1f} | PNL: ${net_pnl:+.2f}")


# ──────────────────────────────────────────────
# Detaylı Prompt (ask_gemini.py kullanır)
# ──────────────────────────────────────────────
def analyze_trades_and_get_prompt(trade_log_csv: str):
    trades, msg = _read_trades(trade_log_csv)
    if not trades:
        return None, msg

    total     = len(trades)
    wins      = [t for t in trades if t["pnl_usd"] > 0]
    losses    = [t for t in trades if t["pnl_usd"] <= 0]
    net_pnl   = sum(t["pnl_usd"] for t in trades)
    win_rate  = len(wins)  / total * 100 if total else 0.0
    avg_win   = sum(t["pnl_usd"] for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss  = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0.0
    rr        = abs(avg_win / avg_loss) if avg_loss else float("inf")
    reasons   = Counter(t["reason"] for t in trades)

    coin_pnl  = defaultdict(float)
    for t in trades:
        coin_pnl[t["symbol"]] += t["pnl_usd"]
    top_w = sorted([x for x in coin_pnl.items() if x[1] > 0],  key=lambda x: -x[1])[:3]
    top_l = sorted([x for x in coin_pnl.items() if x[1] <= 0], key=lambda x:  x[1])[:3]

    prompt = f"""
=== PERFORMANS ÖZETİ ===
Toplam İşlem     : {total}
Net PnL (USD)    : ${net_pnl:+.2f}
Kazanma Oranı    : %{win_rate:.1f}
Ort. Kazanç      : ${avg_win:+.2f}
Ort. Kayıp       : ${avg_loss:+.2f}
Risk/Ödül Oranı  : {rr:.2f}x  (hedef ≥ 1.5)
Kapanış Nedenleri: {dict(reasons)}
En Çok Kazandıran: {", ".join(f"{s}(${p:+.2f})" for s,p in top_w) or "Yok"}
En Çok Kaybettiren: {", ".join(f"{s}(${p:.2f})" for s,p in top_l) or "Yok"}

=== İŞLEM OTOPSİSİ ==="""

    for t in trades:
        d = t["details"]
        prompt += (
            f"\n\n{t['symbol']} | {t['side']} | "
            f"Sonuç:{t['reason']} | PNL:${t['pnl_usd']:+.2f}\n"
            f"  Skor:{d.get('final_score','?')} | "
            f"Durum:{'Trend' if d.get('is_trending') else 'Yatay'} | "
            f"Bileşenler:{d.get('components',{})}"
        )

    prompt += """

=== ANALİZ İSTEKLERİM ===
1. Zararlı işlemlerde ortak desen var mı? (RSI aşırı alım, yatay piyasa vb.)
2. Kazançlı işlemlerde ortak desen var mı?
3. risk/ödül ve kazanma oranına göre config.yaml'da ne değiştirirsin?
   (hard_stop_pct, take_profit_min_pct, score_long_open, score_short_open için sayısal öneri)
"""
    summary = f"{total} işlem | PNL:${net_pnl:+.2f} | Kazanma:%{win_rate:.1f}"
    return prompt, summary
