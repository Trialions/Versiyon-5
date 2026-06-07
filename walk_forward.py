# walk_forward.py — Aylık Walk-Forward Analiz Aracı
# Tek bir uzun backtest yerine, belirtilen aralığı AY AY böler,
# her ayı bağımsız test eder ve dağılımı raporlar.
#
# Amaç: "en yüksek tek-dönem PnL" yerine ROBUSTLUK ölçmek.
#   - Kaç ay pozitif / negatif?
#   - En kötü ay ne kadar kötü? (asıl risk göstergesi)
#   - Aylar arası tutarlılık (standart sapma)
#
# Kullanım:
#   python walk_forward.py --start 2025-01-01 --end 2026-01-01 --interval 1h --top 20
#
# Her ay aynı config ile çalışır (config_online.yaml), böylece
# bir parametre setinin TÜM rejimlerde nasıl davrandığını görürüz.

import sys
import csv
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque

import yaml

import os as _os
_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))

from backtest import (
    Backtester, fetch_klines, _load_cache, _save_cache,
    _run_timeline, _max_drawdown, _sharpe,
)


def _month_ranges(start_date: str, end_date: str):
    """Başlangıç-bitiş arasını aylık dilimlere böler."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end   = datetime.strptime(end_date,   "%Y-%m-%d")
    ranges = []
    cur = start
    while cur < end:
        # Bir sonraki ayın ilk günü
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            nxt = cur.replace(month=cur.month + 1, day=1)
        seg_end = min(nxt, end)
        ranges.append((cur.strftime("%Y-%m-%d"), seg_end.strftime("%Y-%m-%d")))
        cur = nxt
    return ranges


def _fetch_all(symbols, interval, start_ms, end_ms):
    """Tüm sembollerin verisini çeker (cache'li)."""
    all_candles = {}
    total_days = (end_ms - start_ms) // (86400 * 1000)
    for i, sym in enumerate(symbols, 1):
        cached = _load_cache(sym, interval, total_days,
                             datetime.utcfromtimestamp(start_ms/1000).strftime("%Y-%m-%d"),
                             datetime.utcfromtimestamp(end_ms/1000).strftime("%Y-%m-%d"))
        if cached is not None:
            all_candles[sym] = cached
            print(f"  [{i:2}/{len(symbols)}] {sym:<14} cache ({len(cached)} mum)")
        else:
            print(f"  [{i:2}/{len(symbols)}] {sym:<14} indiriliyor...", end=" ", flush=True)
            candles = fetch_klines(sym, interval, start_ms, end_ms)
            if candles:
                _save_cache(sym, interval, total_days, candles,
                            datetime.utcfromtimestamp(start_ms/1000).strftime("%Y-%m-%d"),
                            datetime.utcfromtimestamp(end_ms/1000).strftime("%Y-%m-%d"))
                all_candles[sym] = candles
                print(f"{len(candles)} mum")
            else:
                print("veri yok")
    return all_candles


def _slice_candles(all_candles, seg_start_ms, seg_end_ms):
    """Tüm veriden belirli ay dilimini keser."""
    sliced = {}
    for sym, candles in all_candles.items():
        seg = [c for c in candles if seg_start_ms <= c["open_time"] < seg_end_ms]
        if seg:
            sliced[sym] = seg
    return sliced


def run_walk_forward(symbols, interval, start_date, end_date, cfg, out_dir=None):
    months = _month_ranges(start_date, end_date)
    start_ms = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ms   = int(datetime.strptime(end_date,   "%Y-%m-%d").timestamp() * 1000)

    sep = "=" * 64
    print(f"\n{sep}")
    print(f"  WALK-FORWARD ANALİZİ  —  {len(months)} ay")
    print(f"  {start_date} → {end_date}  |  {len(symbols)} sembol  |  {interval}")
    print(sep)

    print("\n  Veri yükleniyor...")
    all_candles = _fetch_all(symbols, interval, start_ms, end_ms)
    if not all_candles:
        print("\n[HATA] Veri yüklenemedi.")
        return

    results = []
    print(f"\n{sep}")
    print(f"  {'Ay':<10} {'İşlem':>6} {'WR':>6} {'PnL':>9} {'DD':>7} {'Sharpe':>7}")
    print("-" * 64)

    for seg_start, seg_end in months:
        s_ms = int(datetime.strptime(seg_start, "%Y-%m-%d").timestamp() * 1000)
        e_ms = int(datetime.strptime(seg_end,   "%Y-%m-%d").timestamp() * 1000)
        seg_candles = _slice_candles(all_candles, s_ms, e_ms)
        if not seg_candles:
            continue

        # Her ay TEMİZ başlar — bağımsız test
        bt = Backtester(cfg)
        timeline = []
        for sym, candles in seg_candles.items():
            for c in candles:
                timeline.append((c["open_time"], sym, c))
        timeline.sort(key=lambda x: x[0])

        lp = _run_timeline(bt, timeline, seg_candles)
        bt.force_close_all(lp)

        t = bt.trades
        n = len(t)
        if n == 0:
            results.append({"month": seg_start[:7], "n": 0, "wr": 0,
                            "pnl": 0, "dd": 0, "sharpe": 0})
            print(f"  {seg_start[:7]:<10} {'0':>6} {'—':>6} {'$0':>9} {'—':>7} {'—':>7}")
            continue

        wins = sum(1 for x in t if x["net_pnl"] > 0)
        wr   = wins / n * 100
        pnl  = sum(x["net_pnl"] for x in t)
        dd   = _max_drawdown(bt.equity_curve)
        sh   = _sharpe(bt.equity_curve)
        results.append({"month": seg_start[:7], "n": n, "wr": wr,
                        "pnl": pnl, "dd": dd, "sharpe": sh})
        flag = " ⚠️" if pnl < -100 else ("" if pnl >= 0 else " ⚡")
        print(f"  {seg_start[:7]:<10} {n:>6} {wr:>5.0f}% ${pnl:>+8.0f} {dd:>6.1f}% {sh:>7.2f}{flag}")

    # ── Dağılım özeti — asıl değerli kısım ──
    active = [r for r in results if r["n"] > 0]
    if not active:
        print("\n[UYARI] Hiç işlem oluşmadı.")
        return

    pnls   = [r["pnl"] for r in active]
    total  = sum(pnls)
    pos_m  = sum(1 for p in pnls if p > 0)
    neg_m  = sum(1 for p in pnls if p < 0)
    worst  = min(pnls)
    best   = max(pnls)
    avg    = total / len(pnls)
    worst_month = min(active, key=lambda r: r["pnl"])["month"]
    best_month  = max(active, key=lambda r: r["pnl"])["month"]
    # Standart sapma — tutarlılık
    var = sum((p - avg) ** 2 for p in pnls) / len(pnls)
    std = var ** 0.5

    print(f"\n{sep}")
    print(f"  ROBUSTLUK ÖZETİ")
    print(sep)
    print(f"  Toplam PnL          : ${total:+.0f}")
    print(f"  Aylık ortalama      : ${avg:+.0f}")
    print(f"  Pozitif ay          : {pos_m}/{len(active)}")
    print(f"  Negatif ay          : {neg_m}/{len(active)}")
    print(f"  EN KÖTÜ AY          : ${worst:+.0f}  ({worst_month})  ← asıl risk")
    print(f"  En iyi ay           : ${best:+.0f}  ({best_month})")
    print(f"  Aylık std sapma     : ${std:.0f}  (düşük = tutarlı)")
    print(f"  Tutarlılık skoru    : {avg/std:.2f}  (yüksek = iyi)" if std > 0 else "")
    print(sep)

    # Yorum
    print(f"\n  DEĞERLENDİRME:")
    if neg_m > pos_m:
        print(f"  ✗ Aylar çoğunlukla negatif — strateji rejime fazla bağımlı.")
    elif worst < -200:
        print(f"  ⚠ En kötü ay ${worst:.0f} — tek kötü dönem birikimi siliyor.")
    elif std > avg * 2 and avg > 0:
        print(f"  ⚠ Yüksek değişkenlik — kazanç birkaç şanslı aya bağlı.")
    else:
        print(f"  ✓ Dengeli dağılım — strateji farklı rejimlerde dayanıklı.")

    # ── Sonuçları klasöre kaydet ──
    if out_dir:
        import os
        os.makedirs(out_dir, exist_ok=True)
        # Aylık CSV
        csv_path = os.path.join(out_dir, "wf_monthly.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["month","n","wr","pnl","dd","sharpe"], delimiter=";")
            w.writeheader(); w.writerows(results)
        # Özet JSON
        summary = {
            "start_date": start_date, "end_date": end_date,
            "interval": interval, "symbols": len(symbols),
            "total_pnl": round(total, 2), "avg_monthly": round(avg, 2),
            "pos_months": pos_m, "neg_months": neg_m,
            "best_pnl": round(best, 2), "best_month": best_month,
            "worst_pnl": round(worst, 2), "worst_month": worst_month,
            "std_dev": round(std, 2),
            "consistency_score": round(avg/std, 3) if std > 0 else 0,
            "verdict": ("positive" if neg_m <= pos_m and worst >= -200 else "negative"),
        }
        json_path = os.path.join(out_dir, "wf_summary.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n  Sonuçlar kaydedildi → {out_dir}")

    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Walk-Forward Aylık Analiz")
    p.add_argument("--start",    type=str, required=True, help="YYYY-MM-DD")
    p.add_argument("--end",      type=str, required=True, help="YYYY-MM-DD")
    p.add_argument("--interval", type=str, default="1h")
    p.add_argument("--top",      type=int, default=20)
    p.add_argument("--symbols",  type=str, default="")
    p.add_argument("--out",      type=str, default="")
    args = p.parse_args()

    cfg_path = _os.path.join(_SCRIPT_DIR, "config_online.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        sym_path = _os.path.join(_SCRIPT_DIR, "symbols_top70.json")
        symbols = json.loads(Path(sym_path).read_text(encoding="utf-8"))[:args.top]

    run_walk_forward(symbols, args.interval, args.start, args.end, cfg)
    run_walk_forward(symbols, args.interval, args.start, args.end, cfg,
                     out_dir=args.out or None)
