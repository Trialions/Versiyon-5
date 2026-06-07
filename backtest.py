# backtest.py — Çoklu sembol eş zamanlı backtest motoru v6
# YENİLİKLER v5→v6:
#   - Slippage desteği eklendi (config: slippage_pct)
#   - Out-of-sample test: --oos flag ile %70 train / %30 test ayrımı
#   - OOS overfitting uyarısı (train/test win rate farkı)
import time
import csv
import json
import math
import copy
import yaml
import requests
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from strategy_core import score_symbol
from logger import log_info, log_error
from symbol_manager import SymbolManager
from market_regime import MarketRegimeDetector

import os as _os
_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))

BINANCE_API   = "https://api.binance.com"
REQUEST_DELAY = 0.12
CACHE_DIR     = Path(_SCRIPT_DIR) / "backtest_data"


def _cache_path(symbol, interval, days, start_date=None, end_date=None):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = (f"{symbol}_{interval}_{start_date}_{end_date}"
           if start_date and end_date else f"{symbol}_{interval}_{days}d")
    return CACHE_DIR / f"{key}.json"


def _load_cache(symbol, interval, days, start_date=None, end_date=None):
    p = _cache_path(symbol, interval, days, start_date, end_date)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("saved_at", 0) > 86400:
            return None
        return data["candles"]
    except Exception as e:
        log_error(f"Cache okuma {symbol}: {e}")
        return None


def _save_cache(symbol, interval, days, candles, start_date=None, end_date=None):
    p = _cache_path(symbol, interval, days, start_date, end_date)
    try:
        p.write_text(json.dumps({"saved_at": time.time(), "candles": candles},
                                ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log_error(f"Cache yazma {symbol}: {e}")


def fetch_klines(symbol, interval, start_ms, end_ms):
    url     = f"{BINANCE_API}/api/v3/klines"
    candles = []
    current = start_ms
    while current < end_ms:
        try:
            r = requests.get(url, params={
                "symbol": symbol, "interval": interval,
                "startTime": current, "endTime": end_ms, "limit": 1000,
            }, timeout=10)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for k in batch:
                candles.append({
                    "open_time": k[0], "open": float(k[1]),
                    "high": float(k[2]), "low": float(k[3]),
                    "close": float(k[4]), "volume": float(k[5]),
                })
            current = batch[-1][0] + 1
            if len(batch) < 1000:
                break
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            log_error(f"Kline {symbol}: {e}")
            break
    return candles


def _max_drawdown(equity_curve):
    if not equity_curve:
        return 0.0
    peak   = equity_curve[0][1]
    max_dd = 0.0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _sharpe(equity_curve, risk_free=0.0):
    if len(equity_curve) < 2:
        return 0.0
    vals = [eq for _, eq in equity_curve]
    rets = [(vals[i] - vals[i-1]) / vals[i-1] for i in range(1, len(vals)) if vals[i-1] > 0]
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var  = sum((r - mean) ** 2 for r in rets) / len(rets)
    std  = math.sqrt(var)
    return round((mean - risk_free) / std * math.sqrt(len(rets)), 3) if std > 0 else 0.0


class Backtester:
    def __init__(self, cfg: dict):
        risk  = cfg.get("risk",       {})
        lim   = cfg.get("limits",     {})
        thr   = cfg.get("thresholds", {})
        misc  = cfg.get("misc",       {})

        self.starting_equity  = float(misc.get("starting_equity_usdt",    1000.0))
        self.equity           = self.starting_equity
        self.risk_per_trade   = float(risk.get("risk_per_trade_pct",       1.0)) / 100
        self.sl_pct           = float(risk.get("hard_stop_pct",            2.5)) / 100
        self.tp_pct           = float(risk.get("take_profit_min_pct",      4.0)) / 100
        self.use_atr_stop     = bool( risk.get("use_atr_stop",             True))
        self.atr_multiplier   = float(risk.get("atr_multiplier",           2.5))
        self.max_stop_pct     = float(risk.get("max_stop_pct",             4.5)) / 100
        self.trail            = bool( risk.get("use_trailing",             True))
        self.trail_step       = float(risk.get("trailing_step_pct",        1.0)) / 100
        self.min_hold         = int(  risk.get("min_hold_minutes",         60))  * 60
        self.min_profit_close = float(risk.get("min_profit_close_pct",     3.0)) / 100
        self.score_long_open  = float(thr.get("score_long_open",           80))
        self.score_short_open = float(thr.get("score_short_open",           5))
        self.score_close      = float(thr.get("score_close",               30))
        self.max_open_pos     = int(  lim.get("max_open_positions",         4))
        self.max_trades_day   = int(  lim.get("max_trades_per_day",         8))
        self.daily_target_pct = float(lim.get("daily_target_pct",         10.0)) / 100
        self.max_hold_sec     = int(  lim.get("max_hold_hours",             48)) * 3600
        self.daily_loss_limit = float(lim.get("daily_loss_limit_pct",      5.0)) / 100
        self.vol_mult         = float(misc.get("volume_burst_multiplier",   2.0))
        self.min_notional     = float(misc.get("min_notional_usdt",     30000.0))
        self.commission       = float(misc.get("commission_pct",            0.04)) / 100
        self.slippage         = float(misc.get("slippage_pct",              0.03)) / 100

        btc_f = cfg.get("btc_filter", {})
        self.btc_filter_enabled  = bool( btc_f.get("enabled",        True))
        self.btc_filter_lookback = int(  btc_f.get("lookback_candles", 4))
        self.btc_filter_drop_pct = float(btc_f.get("drop_pct",         1.5)) / 100
        adx_f = cfg.get("adx_filter", {})
        self.adx_filter_enabled   = bool( adx_f.get("enabled",    False))
        self.adx_filter_threshold = float(adx_f.get("threshold",  25.0))
        ptp = cfg.get("partial_tp", {})
        self.ptp_enabled   = bool( ptp.get("enabled",    True))
        self.ptp_r_mult    = float(ptp.get("tp1_r_mult", 0.75))
        self.ptp_close_pct = float(ptp.get("close_pct",  0.50))
        qs = cfg.get("quality_score", {})
        self.qs_enabled   = bool(qs.get("enabled",       True))
        self.qs_min_half  = int( qs.get("min_half_pos",  5))
        self.qs_min_full  = int( qs.get("min_full_pos",  7))
        self.sym_mgr  = SymbolManager(cfg)
        self.regime   = MarketRegimeDetector(cfg)

        self.btc_closes       = []
        self.open_positions   = {}
        self.trades           = []
        self.trade_count_day  = 0
        self.daily_pnl        = 0.0
        self.last_day         = ""
        self.daily_fired      = False
        self.equity_curve     = [(0, self.starting_equity)]

    def _lot(self, price, sl_pct=None):
        sl_pct    = sl_pct or self.sl_pct
        risk_usdt = self.equity * self.risk_per_trade
        return max(0.0001, risk_usdt / (price * max(sl_pct, 0.001)))

    def _reset_day(self, ts_ms):
        day = datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
        if day != self.last_day:
            self.trade_count_day = 0
            self.daily_pnl       = 0.0
            self.daily_fired     = False
            self.last_day        = day

    def _daily_target_hit(self):
        if self.daily_fired:
            return True
        if self.daily_pnl >= self.equity * self.daily_target_pct:
            self.daily_fired = True
            return True
        return False

    def _daily_loss_hit(self):
        return self.daily_pnl <= -(self.equity * self.daily_loss_limit)

    def _get_btc_sentiment(self):
        if len(self.btc_closes) < 50:
            return "NEUTRAL"
        closes = self.btc_closes[-100:]
        k20, k50 = 2 / 21, 2 / 51
        ema20 = ema50 = closes[0]
        for c in closes[1:]:
            ema20 = c * k20 + ema20 * (1 - k20)
            ema50 = c * k50 + ema50 * (1 - k50)
        diff = (ema20 - ema50) / ema50 * 100 if ema50 > 0 else 0
        if diff >  0.5: return "BULLISH"
        if diff < -0.5: return "BEARISH"
        return "NEUTRAL"

    def _btc_trend_ok(self, side: str) -> bool:
        if not self.btc_filter_enabled:
            return True
        n = self.btc_filter_lookback
        if len(self.btc_closes) < n + 1:
            return True
        ref = self.btc_closes[-(n + 1)]
        now = self.btc_closes[-1]
        chg = (now - ref) / ref if ref > 0 else 0
        if side == "LONG"  and chg <= -self.btc_filter_drop_pct:
            return False
        if side == "SHORT" and chg >=  self.btc_filter_drop_pct:
            return False
        return True
    def _quality_score(self, symbol: str, result: dict, side: str) -> int:
        """
        0-10 arası kalite puanı.
        < qs_min_half  → işlem yok
        qs_min_half..qs_min_full → yarım pozisyon (qty * 0.5)
        >= qs_min_full → tam pozisyon
        """
        if not self.qs_enabled:
            return 10
        score = 0
        comp  = result.get("components", {})

        # HTF uyumu +2
        htf = comp.get("htf_score", 50.0)
        if side == "LONG"  and htf >= self.mtf_long_min:  score += 2
        if side == "SHORT" and htf <= self.mtf_short_max: score += 2

        # Volatilite uygun +2 (ATR ne çok düşük ne çok yüksek)
        atr_pct = comp.get("atr_pct", 0.0)
        if 0.3 <= atr_pct <= 3.0: score += 2

        # Rejim bullish +2
        regime = self.regime._last_regime
        if regime == "TREND":  score += 2
        elif regime == "KONSOL": score += 1

        # Hacim artışı +2
        vol_ratio = comp.get("volume", 50.0)
        if vol_ratio >= 65: score += 2
        elif vol_ratio >= 55: score += 1

        # BTC trend uyumu +2
        if self._btc_trend_ok(side): score += 2

        return score
    
    def _exit_reason(self, pos, price, change, score):
        pos_sl = pos.get("sl_pct", self.sl_pct)
        if change <= -pos_sl:
            return "SL"
        if change >= self.tp_pct and change >= self.min_profit_close:
            return "TP"
        if change >= self.min_profit_close:
            if pos["side"] == "LONG"  and score < self.score_close:
                return "ScoreClose"
            if pos["side"] == "SHORT" and score > self.score_close:
                return "ScoreClose"
            locked = pos.get("trail_locked")
            if self.trail and locked is not None and change < locked - self.trail_step:
                return "Trail"
        return None

    def step(self, symbol, candle, prices, highs, lows, volumes):
        price  = candle["close"]
        ts_ms  = candle["open_time"]
        ts_sec = ts_ms / 1000

        if symbol == "BTCUSDT":
            self.btc_closes.append(price)
            if len(self.btc_closes) > 500:
                self.btc_closes = self.btc_closes[-500:]
            self.regime.detect(
                self.btc_closes,
                btc_highs=list(highs)   if highs   else None,
                btc_lows =list(lows)    if lows    else None,
                btc_vols =list(volumes) if volumes else None,
            )

        self._reset_day(ts_ms)

        result = score_symbol(prices, highs, lows, volumes)
        score  = result["final_score"]

        # ── Açık pozisyon yönetimi ─────────────────────────────
        if symbol in self.open_positions:
            pos    = self.open_positions[symbol]
            age    = ts_sec - pos["ts_open"]
            mult   = 1 if pos["side"] == "LONG" else -1
            change = (price - pos["entry"]) / pos["entry"] * mult

            # TP1 sonrası breakeven kontrolü
            if pos.get("tp1_done"):
                be_buffer = (self.commission + self.slippage) * 2
                if change <= -be_buffer:
                    self._close(symbol, price, change, "Breakeven", ts_ms)
                    return
            else:
                if change <= -pos.get("sl_pct", self.sl_pct):
                    self._close(symbol, price, change, "SL", ts_ms)
                    return

            if age >= self.max_hold_sec:
                self._close(symbol, price, change, "MaxHold", ts_ms)
                return

            # Partial TP
            if self.ptp_enabled and not pos.get("tp1_done"):
                tp1_level = pos.get("sl_pct", self.sl_pct) * self.ptp_r_mult
                if change >= tp1_level:
                    close_qty = pos["qty"] * self.ptp_close_pct
                    self._close(symbol, price, change, "TP1", ts_ms, close_qty=close_qty)
                    p = self.open_positions.get(symbol)
                    if p:
                        p["tp1_done"]     = True
                        p["trail_locked"] = change
                    return

            if self.trail and change > 0:
                locked = pos.get("trail_locked")
                if locked is None or change > locked + self.trail_step:
                    pos["trail_locked"] = change
            if age >= self.min_hold:
                reason = self._exit_reason(pos, price, change, score)
                if reason and reason != "SL":
                    self._close(symbol, price, change, reason, ts_ms)
            return

        # ── Yeni pozisyon kontrol kapıları ─────────────────────
        if not self.regime.is_open():                     return
        if len(self.open_positions) >= self.max_open_pos: return
        if self.trade_count_day >= self.max_trades_day:   return
        if self._daily_target_hit():                      return
        if self._daily_loss_hit():                        return

        if len(prices) >= 20:
            if (sum(prices[-20:]) / 20) * (sum(volumes[-20:]) / 20) < self.min_notional:
                return

        if len(volumes) >= 20:
            rv = sum(volumes[-3:]) / 3
            hv = sum(volumes[-20:-3]) / 17
            if hv > 0 and rv < hv * self.vol_mult:
                return

        btc = self.open_positions.get("BTCUSDT")
        if btc:
            if btc["side"] == "SHORT" and score >= self.score_long_open:  return
            if btc["side"] == "LONG"  and score <= self.score_short_open:
                if score > self.score_short_open / 2: return

        sentiment = self._get_btc_sentiment()
        side      = None
        if score >= self.score_long_open  and sentiment != "BEARISH":  side = "LONG"
        elif score <= self.score_short_open and sentiment != "BULLISH": side = "SHORT"
        if not side:
            return
        if not self._btc_trend_ok(side):
            return
        adx_val = result.get("components", {}).get("adx", 0.0)
        if self.adx_filter_enabled and adx_val > 0 and adx_val < self.adx_filter_threshold:
            return

        if self.use_atr_stop and "atr_pct" in result.get("components", {}):
            atr_pct_val = result["components"]["atr_pct"] / 100
            final_sl    = min(atr_pct_val * self.atr_multiplier, self.max_stop_pct)
            final_sl    = max(final_sl, 0.005)
        else:
            final_sl = self.sl_pct

        qs = self._quality_score(symbol, result, side)
        if qs < self.qs_min_half:
            return
        qty = self._lot(price, sl_pct=final_sl)
        qty *= self.sym_mgr.size_multiplier(symbol)
        qty *= self.regime.size_multiplier()
        if qs < self.qs_min_full:
            qty *= 0.5
        comp = result.get("components", {})
        vol_ratio = round(volumes[-1] / (sum(volumes[-20:-1]) / 19), 2) if len(volumes) >= 20 else 0.0
        self.open_positions[symbol] = {
            "side":    side,   "entry":   price,
            "qty":     qty,    "sl_pct":  final_sl,
            "ts_open": ts_sec, "score":   score,
            "atr_pct":    round(comp.get("atr_pct",  0.0), 3),
            "adx":        round(comp.get("adx",       0.0), 1),
            "rsi":        round(comp.get("rsi",        0.0), 1),
            "htf_score":  round(comp.get("htf_score", 0.0), 1),
            "vol_ratio":  vol_ratio,
            "btc_trend":  1 if self._btc_trend_ok(side) else 0,
        }
        self.trade_count_day += 1

    def _close(self, symbol, price, change, reason, ts_ms, close_qty=None):
        pos = self.open_positions.get(symbol)
        if not pos:
            return
        full_qty = pos["qty"]
        qty      = full_qty if close_qty is None else min(close_qty, full_qty)
        partial  = close_qty is not None and qty < full_qty
        if partial:
            pos["qty"] = full_qty - qty
        else:
            self.open_positions.pop(symbol, None)
        entry    = pos["entry"]
        gross    = ((price - entry) if pos["side"] == "LONG"
                    else (entry - price)) * qty
        comm     = (entry * qty + price * qty) * self.commission
        slippage = price * qty * self.slippage
        net      = gross - comm - slippage

        self.equity    += net
        self.daily_pnl += net
        self.equity_curve.append((ts_ms, round(self.equity, 4)))

        self.trades.append({
            "symbol":     symbol,
            "side":       pos["side"],
            "entry":      entry,
            "exit":       price,
            "qty":        round(qty, 6),
            "change_pct": round(change * 100, 3),
            "gross_pnl":  round(gross, 3),
            "commission": round(comm, 4),
            "slippage":   round(slippage, 4),
            "net_pnl":    round(net, 3),
            "reason":     reason,
            "partial":    partial,
            "score":      round(pos["score"], 2),
            "sl_pct":     round(pos.get("sl_pct", self.sl_pct) * 100, 2),
            "atr_pct":    pos.get("atr_pct",   0.0),
            "adx":        pos.get("adx",        0.0),
            "rsi":        pos.get("rsi",         0.0),
            "htf_score":  pos.get("htf_score",  0.0),
            "vol_ratio":  pos.get("vol_ratio",  0.0),
            "btc_trend":  pos.get("btc_trend",  1),
            "open_time":  datetime.utcfromtimestamp(pos["ts_open"]).strftime("%Y-%m-%d %H:%M"),
            "close_time": datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M"),
            "hold_min":   round((ts_ms / 1000 - pos["ts_open"]) / 60, 1),
        })

        if not partial:
            self.sym_mgr.record_trade(symbol, net)

    def force_close_all(self, last_prices):
        for sym, pos in list(self.open_positions.items()):
            price  = last_prices.get(sym, pos["entry"])
            mult   = 1 if pos["side"] == "LONG" else -1
            change = (price - pos["entry"]) / pos["entry"] * mult
            self._close(sym, price, change, "EndOfTest",
                        int(time.time() * 1000))


def generate_report(trades, starting_equity, final_equity,
                    equity_curve, out_dir, label=""):
    out_dir.mkdir(parents=True, exist_ok=True)
    if not trades:
        print("\n[UYARI] Hiç işlem oluşmadı. Parametreleri gevşet.")
        s_csv = out_dir / "backtest_summary.csv"
        with open(s_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Metrik", "Değer"])
            w.writerows([
                ["BaslangicEquity", f"${starting_equity:.2f}"],
                ["BitisEquity",     f"${final_equity:.2f}"],
                ["ToplamIslem",     0],
            ])
        return {}

    wins   = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    total  = len(trades)
    win_rate   = len(wins) / total * 100 if total else 0
    net_pnl    = sum(t["net_pnl"] for t in trades)
    total_ret  = (final_equity - starting_equity) / starting_equity * 100
    commissions = sum(t["commission"] for t in trades)
    slippages   = sum(t["slippage"]   for t in trades)
    avg_win    = sum(t["net_pnl"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss   = sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0
    rr         = abs(avg_win / avg_loss) if avg_loss else 0
    max_gain   = max(t["net_pnl"] for t in trades)
    max_loss   = min(t["net_pnl"] for t in trades)
    avg_hold   = sum(t["hold_min"] for t in trades) / total if total else 0
    max_dd     = _max_drawdown(equity_curve)
    sharpe     = _sharpe(equity_curve)
    recovery   = round(net_pnl / (max_dd / 100 * starting_equity), 2) if max_dd > 0 else "∞"

    streak = consec_win = consec_loss = cur_w = cur_l = 0
    for t in trades:
        if t["net_pnl"] > 0:
            cur_w += 1; cur_l = 0
            consec_win  = max(consec_win,  cur_w)
        else:
            cur_l += 1; cur_w = 0
            consec_loss = max(consec_loss, cur_l)

    reasons = defaultdict(int)
    for t in trades:
        reasons[t["reason"]] += 1

    sym_pnl = defaultdict(float)
    for t in trades:
        sym_pnl[t["symbol"]] += t["net_pnl"]
    top_winners = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)[:3]
    top_losers  = sorted(sym_pnl.items(), key=lambda x: x[1])[:3]

    sep = "=" * 60
    ttl = f"  BACKTEST RAPORU{f'  [{label}]' if label else ''}"
    print(f"\n{sep}\n{ttl}\n{sep}")
    print(f"  Başlangıç Equity  : ${starting_equity:.2f}")
    print(f"  Bitiş Equity      : ${final_equity:.2f}")
    print(f"  Toplam Getiri     : %{total_ret:+.2f}")
    print(f"  Net PnL           : ${net_pnl:+.2f}")
    print(f"  Max Drawdown      : %{max_dd:.2f}")
    print(f"  Recovery Factor   : {recovery}")
    print(f"  Sharpe Oranı      : {sharpe}")
    print(f"  Toplam Komisyon   : ${commissions:.2f}")
    print(f"  Toplam Slippage   : ${slippages:.2f}")
    print(sep)
    print(f"  Toplam İşlem      : {total}")
    print(f"  Kazanan           : {len(wins)}")
    print(f"  Kaybeden          : {len(losses)}")
    print(f"  Kazanma Oranı     : %{win_rate:.1f}")
    print(f"  Ort. Kazanç       : ${avg_win:+.2f}")
    print(f"  Ort. Kayıp        : ${avg_loss:+.2f}")
    print(f"  Risk/Ödül         : {rr:.2f}x")
    print(f"  En Büyük Kazanç   : ${max_gain:+.2f}")
    print(f"  En Büyük Kayıp    : ${max_loss:+.2f}")
    print(f"  Ort. Tutma        : {avg_hold:.1f} dk")
    print(f"  Ardışık Kazanç    : {consec_win}")
    print(f"  Ardışık Kayıp     : {consec_loss}")
    print(sep)
    print(f"  Kapanış Nedenleri :")
    for r, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {r:<15}: {n}  (%{n/total*100:.0f})")
    print(sep)
    print(f"  En Çok Kazandıran :")
    for s, p in top_winners:
        print(f"    {s:<14}: ${p:+.2f}")
    print(f"  En Çok Kaybettiren:")
    for s, p in top_losers:
        print(f"    {s:<14}: ${p:+.2f}")
    print(sep)

    if trades:
        t_csv = out_dir / "backtest_trades.csv"
        with open(t_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=trades[0].keys(), delimiter=";")
            w.writeheader(); w.writerows(trades)
        print(f"\n  İşlem detayları  : {t_csv}")

    if equity_curve:
        eq_csv = out_dir / "equity_curve.csv"
        with open(eq_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Timestamp", "Equity"])
            for ts, eq in equity_curve:
                dt = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
                w.writerow([dt, eq])
        print(f"  Equity eğrisi    : {eq_csv}")

    s_csv = out_dir / "backtest_summary.csv"
    with open(s_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Metrik", "Değer"])
        w.writerows([
            ["BaslangicEquity",  f"${starting_equity:.2f}"],
            ["BitisEquity",      f"${final_equity:.2f}"],
            ["ToplamGetiri",     f"%{total_ret:+.2f}"],
            ["NetPnL",           f"${net_pnl:+.2f}"],
            ["MaxDrawdown",      f"%{max_dd:.2f}"],
            ["RecoveryFactor",   recovery],
            ["SharpeOrani",      sharpe],
            ["ToplamKomisyon",   f"${commissions:.2f}"],
            ["ToplamSlippage",   f"${slippages:.2f}"],
            ["ToplamIslem",      total],
            ["Kazanan",          len(wins)],
            ["Kaybeden",         len(losses)],
            ["KazanmaOrani",     f"%{win_rate:.1f}"],
            ["OrtKazanc",        f"${avg_win:.2f}"],
            ["OrtKayip",         f"${avg_loss:.2f}"],
            ["RiskOdul",         f"{rr:.2f}x"],
            ["ArdisikKazanc",    consec_win],
            ["ArdisikKayip",     consec_loss],
            ["OrtTutmaDakika",   f"{avg_hold:.1f}"],
        ])
    print(f"  Özet raporu      : {s_csv}")
    print(sep)

    return {
        "net_pnl": net_pnl, "win_rate": win_rate, "rr": rr,
        "max_dd": max_dd, "sharpe": sharpe, "total": total,
        "total_ret": total_ret,
    }


PARAM_GRID = {
    "score_long_open":     [78, 83, 88],
    "score_short_open":    [5, 10, 15],
    "hard_stop_pct":       [2.0, 2.5, 3.0],
    "take_profit_min_pct": [3.0, 4.0, 5.0],
    "btc_filter_lookback": [2, 4, 6],
    "btc_filter_drop_pct": [1.0, 1.5, 2.0],
    "adx_filter_threshold":[20, 25, 30],
}


def _build_cfg_variant(base_cfg: dict, params: dict) -> dict:
    cfg = copy.deepcopy(base_cfg)
    cfg.setdefault("thresholds", {})["score_long_open"]   = params["score_long_open"]
    cfg.setdefault("thresholds", {})["score_short_open"]  = params["score_short_open"]
    cfg.setdefault("risk", {})["hard_stop_pct"]           = params["hard_stop_pct"]
    cfg.setdefault("risk", {})["take_profit_min_pct"]     = params["take_profit_min_pct"]
    cfg.setdefault("btc_filter", {})["lookback_candles"]  = params["btc_filter_lookback"]
    cfg.setdefault("btc_filter", {})["drop_pct"]          = params["btc_filter_drop_pct"]
    cfg.setdefault("adx_filter", {})["threshold"]         = params["adx_filter_threshold"]
    return cfg


def _run_timeline(bt: Backtester, timeline: list, all_candles: dict, window: int = 500):
    from collections import deque
    price_buf   = {s: deque(maxlen=window) for s in all_candles}
    high_buf    = {s: deque(maxlen=window) for s in all_candles}
    low_buf     = {s: deque(maxlen=window) for s in all_candles}
    vol_buf     = {s: deque(maxlen=window) for s in all_candles}
    last_prices = {}
    for ts, sym, candle in timeline:
        price_buf[sym].append(candle["close"])
        high_buf[sym].append(candle["high"])
        low_buf[sym].append(candle["low"])
        vol_buf[sym].append(candle["volume"])
        last_prices[sym] = candle["close"]
        if len(price_buf[sym]) >= 50:
            bt.step(sym, candle, list(price_buf[sym]),
                    list(high_buf[sym]), list(low_buf[sym]),
                    list(vol_buf[sym]))
    return last_prices


def run_parameter_search(symbols, interval, days, base_cfg,
                         all_candles, start_ms, save_best_to=None, oos_split=1.0):
    from itertools import product
    timeline = []
    for sym, candles in all_candles.items():
        for c in candles:
            timeline.append((c["open_time"], sym, c))
    timeline.sort(key=lambda x: x[0])

    split_idx  = int(len(timeline) * oos_split)
    train_tl   = timeline[:split_idx]
    test_tl    = timeline[split_idx:]
    split_date = (datetime.utcfromtimestamp(timeline[split_idx][0] / 1000).strftime("%Y-%m-%d")
                  if test_tl else "?")

    keys   = list(PARAM_GRID.keys())
    combos = list(product(*[PARAM_GRID[k] for k in keys]))
    total  = len(combos)

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  PARAMETRE OPTİMİZASYONU  —  {total} kombinasyon")
    if test_tl:
        print(f"  Train : ilk %{int(oos_split*100)} veri  (-> {split_date})")
        print(f"  Test  : son %{int((1-oos_split)*100)} veri  ({split_date} ->)")
    print(sep)

    results = []
    for i, combo in enumerate(combos, 1):
        params  = dict(zip(keys, combo))
        cfg     = _build_cfg_variant(base_cfg, params)
        bt      = Backtester(cfg)
        lp      = _run_timeline(bt, train_tl, all_candles)
        bt.force_close_all(lp)
        t       = bt.trades
        total_t = len(t)
        if total_t < 3:
            continue
        wins   = sum(1 for x in t if x["net_pnl"] > 0)
        wr     = wins / total_t * 100
        net    = sum(x["net_pnl"] for x in t)
        dd     = _max_drawdown(bt.equity_curve)
        sharpe = _sharpe(bt.equity_curve)
        results.append({"params": params, "net_pnl": round(net,2),
                        "win_rate": round(wr,1), "max_dd": dd,
                        "sharpe": sharpe, "trades": total_t})
        bar = "#" * int(i / total * 30)
        print(f"  [{i:3}/{total}] {bar:<30}  "
              f"slong={params['score_long_open']} "
              f"sl={params['hard_stop_pct']} "
              f"tp={params['take_profit_min_pct']}  "
              f"-> WR=%{wr:.0f} PnL=${net:+.0f} DD=%{dd:.1f}",
              flush=True)

    if not results:
        print("\n[OPT] Hiçbir kombinasyon yeterli işlem üretemedi.")
        return None

    best = max(results, key=lambda x: x["sharpe"])
    print(f"\n{sep}")
    print(f"  EN İYİ KOMBİNASYON (Sharpe bazlı):")
    for k, v in best["params"].items():
        print(f"    {k}: {v}")
    print(f"  Train  → WR=%{best['win_rate']:.1f}  PnL=${best['net_pnl']:+.0f}  "
          f"DD=%{best['max_dd']:.1f}  Sharpe={best['sharpe']}")

    if test_tl and test_tl:
        bt2 = Backtester(_build_cfg_variant(base_cfg, best["params"]))
        lp2 = _run_timeline(bt2, test_tl, all_candles)
        bt2.force_close_all(lp2)
        t2  = bt2.trades
        if t2:
            wr2  = sum(1 for x in t2 if x["net_pnl"] > 0) / len(t2) * 100
            net2 = sum(x["net_pnl"] for x in t2)
            dd2  = _max_drawdown(bt2.equity_curve)
            sh2  = _sharpe(bt2.equity_curve)
            print(f"  OOS    -> WR=%{wr2:.1f}  PnL=${net2:+.0f}  DD=%{dd2:.1f}  Sharpe={sh2}")
            if abs(best["win_rate"] - wr2) > 15:
                print("  [UYARI] Train/Test farkı yüksek — overfitting riski!")

    if save_best_to:
        try:
            with open(save_best_to, "r", encoding="utf-8") as f:
                cfg_file = yaml.safe_load(f) or {}
            cfg_file.setdefault("thresholds", {})["score_long_open"]  = best["params"]["score_long_open"]
            cfg_file.setdefault("thresholds", {})["score_short_open"] = best["params"]["score_short_open"]
            cfg_file.setdefault("risk", {})["hard_stop_pct"]          = best["params"]["hard_stop_pct"]
            cfg_file.setdefault("risk", {})["take_profit_min_pct"]    = best["params"]["take_profit_min_pct"]
            with open(save_best_to, "w", encoding="utf-8") as f:
                yaml.dump(cfg_file, f, allow_unicode=True, default_flow_style=False)
            print(f"  [OK] En iyi parametreler {save_best_to} dosyasına yazıldı.")
        except Exception as e:
            print(f"\n  [HATA] Config yazılamadı: {e}")

    return best


def run_backtest(symbols, interval, days, cfg, out_dir,
                 start_date=None, end_date=None, optimize=False,
                 save_config=None, oos=False):
    if start_date and end_date:
        start_ms = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        end_ms   = int(datetime.strptime(end_date,   "%Y-%m-%d").timestamp() * 1000)
        days     = (end_ms - start_ms) // (24 * 60 * 60 * 1000)
    else:
        end_ms   = int(time.time() * 1000)
        start_ms = end_ms - days * 24 * 60 * 60 * 1000

    print(f"\nBacktest {'(OPTİMİZASYON MODU)' if optimize else 'Baslıyor'}"
          f"{' + OOS' if oos else ''}")
    print(f"  Semboller  : {len(symbols)} adet")
    print(f"  Interval   : {interval}")
    print(f"  Sure       : Son {days} gun")
    print(f"  Baslangic  : {datetime.utcfromtimestamp(start_ms/1000).strftime('%Y-%m-%d')}")
    print(f"  Bitis      : {datetime.utcfromtimestamp(end_ms/1000).strftime('%Y-%m-%d')}")
    print(f"  Cache      : {CACHE_DIR}/\n")

    all_candles = {}
    for i, sym in enumerate(symbols, 1):
        cached = _load_cache(sym, interval, days, start_date, end_date)
        if cached is not None:
            print(f"  [{i:2}/{len(symbols)}] {sym:<14} cache ({len(cached)} mum)")
            all_candles[sym] = cached
        else:
            print(f"  [{i:2}/{len(symbols)}] {sym:<14} indiriliyor...", end=" ", flush=True)
            candles = fetch_klines(sym, interval, start_ms, end_ms)
            if candles:
                _save_cache(sym, interval, days, candles, start_date, end_date)
                all_candles[sym] = candles
                print(f"{len(candles)} mum")
            else:
                print("veri yok, atlandi")

    if not all_candles:
        print("\n[HATA] Hic veri yuklenemedi.")
        return

    if optimize:
        run_parameter_search(symbols, interval, days, cfg,
                             all_candles, start_ms,
                             save_best_to=save_config,
                             oos_split=0.70 if oos else 1.0)
        return

    print(f"\n  Zaman ekseni olusturuluyor...")
    timeline = []
    for sym, candles in all_candles.items():
        for c in candles:
            timeline.append((c["open_time"], sym, c))
    timeline.sort(key=lambda x: x[0])
    print(f"  Toplam {len(timeline):,} mum adimi\n")

    from collections import deque
    WINDOW    = 500
    price_buf = {s: deque(maxlen=WINDOW) for s in all_candles}
    high_buf  = {s: deque(maxlen=WINDOW) for s in all_candles}
    low_buf   = {s: deque(maxlen=WINDOW) for s in all_candles}
    vol_buf   = {s: deque(maxlen=WINDOW) for s in all_candles}

    bt          = Backtester(cfg)
    last_prices = {}
    processed   = 0

    for ts, sym, candle in timeline:
        price_buf[sym].append(candle["close"])
        high_buf[sym].append(candle["high"])
        low_buf[sym].append(candle["low"])
        vol_buf[sym].append(candle["volume"])
        last_prices[sym] = candle["close"]
        if len(price_buf[sym]) >= 50:
            bt.step(sym, candle, list(price_buf[sym]),
                    list(high_buf[sym]), list(low_buf[sym]),
                    list(vol_buf[sym]))
        processed += 1
        if processed % 50000 == 0:
            pct = processed / len(timeline) * 100
            print(f"  Ilerleme: %{pct:.1f} - Acik: {len(bt.open_positions)} "
                  f"Islem: {len(bt.trades)}")

    bt.force_close_all(last_prices)
    generate_report(bt.trades, bt.starting_equity, bt.equity,
                    bt.equity_curve, out_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kripto Trade Botu - Backtest v6")
    parser.add_argument("--days",        type=int,   default=30)
    parser.add_argument("--start",       type=str,   default="")
    parser.add_argument("--end",         type=str,   default="")
    parser.add_argument("--interval",    type=str,   default="1h")
    parser.add_argument("--symbols",     type=str,   default="")
    parser.add_argument("--top",         type=int,   default=20)
    parser.add_argument("--out",         type=str,   default="backtest_results")
    parser.add_argument("--clear-cache", action="store_true")
    parser.add_argument("--optimize",    action="store_true")
    parser.add_argument("--save-config", type=str,   default="")
    parser.add_argument("--oos",         action="store_true")
    args = parser.parse_args()

    if args.clear_cache and CACHE_DIR.exists():
        import shutil; shutil.rmtree(CACHE_DIR)
        print(f"[OK] Cache temizlendi: {CACHE_DIR}/")

    try:
        cfg_path = _os.path.join(_SCRIPT_DIR, "config_online.yaml")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        print("[HATA] config_online.yaml bulunamadi!"); exit(1)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        try:
            sym_path = _os.path.join(_SCRIPT_DIR, "symbols_top70.json")
            symbols = json.loads(
                Path(sym_path).read_text(encoding="utf-8")
            )[:args.top]
        except FileNotFoundError:
            print("[HATA] symbols_top70.json bulunamadi!"); exit(1)

    run_backtest(
        symbols     = symbols,
        interval    = args.interval,
        days        = args.days,
        cfg         = cfg,
        out_dir     = Path(args.out),
        start_date  = args.start  or None,
        end_date    = args.end    or None,
        optimize    = args.optimize,
        save_config = args.save_config or None,
        oos         = args.oos,
    )
