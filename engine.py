# engine.py — İşlem motoru v6 (çoklu timeframe konfirmasyon)
# DEĞİŞİKLİKLER v5→v6:
#   1. HTF (higher timeframe, varsayılan 1h) skor konfirmasyonu eklendi
#   2. htf_close_series / htf_high_series / htf_low_series / htf_vol_series eklendi
#   3. seed_from_candles_htf() ve on_candle_htf() eklendi
#   4. _try_open'da HTF konfirmasyon şartı: 5m ve 1h aynı yönü göstermeli
# DEĞİŞİKLİKLER v6→v7 (adaptive_sl):
#   5. adaptive_sl modülü entegre edildi
#   6. _open(): SL mesafesi rejime göre dinamik (TREND geniş, KONSOL normal)
#   7. _try_open(): giriş score eşiği rejime göre otomatik ayarlanıyor
#   8. _manage() / _exit_reason(): trail_step pozisyona özel (rejim anında saklanır)
import time
import csv
import threading
from collections import deque
from pathlib import Path

import adaptive_sl
from strategy_core import score_symbol
from data_macro import get_market_sentiment, get_sentiment_score
from market_regime import MarketRegimeDetector
from symbol_manager import SymbolManager
from logger import log_info, log_error, log_event


class TradeEngine:
    def __init__(self, symbols: list, cfg: dict = None, data_dir: str = "data"):
        self.cfg      = cfg or {}
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        risk  = self.cfg.get("risk",        {})
        lim   = self.cfg.get("limits",      {})
        pyr   = self.cfg.get("pyramiding",  {})
        thr   = self.cfg.get("thresholds",  {})
        misc  = self.cfg.get("misc",        {})
        mtf   = self.cfg.get("mtf",         {})
        adx_f = self.cfg.get("adx_filter",  {})
        self.adx_filter_enabled   = bool( adx_f.get("enabled",   True))
        self.adx_filter_threshold = float(adx_f.get("threshold", 25.0))

        # ── ATR Minimum Filtresi ──────────────────────────────
        atr_f = cfg.get("atr_filter", {})
        self.atr_filter_enabled = bool( atr_f.get("enabled",     False))
        self.atr_filter_min     = float(atr_f.get("min_atr_pct", 0.8))

        # ── RSI Filtresi ──────────────────────────────────────
        rsi_f = self.cfg.get("rsi_filter", {})
        self.rsi_filter_enabled = bool( rsi_f.get("enabled",   True))
        self.rsi_max_long       = float(rsi_f.get("max_long",  73.0))
        self.rsi_min_short      = float(rsi_f.get("min_short", 30.0))

        self.equity           = float(misc.get("starting_equity_usdt", 1000.0))
        self.tp_pct           = float(risk.get("take_profit_min_pct",  3.0)) / 100
        self.sl_pct           = float(risk.get("hard_stop_pct",        1.5)) / 100
        self.use_atr_stop     = bool(risk.get("use_atr_stop", True))
        self.atr_multiplier   = float(risk.get("atr_multiplier", 2.0))
        self.max_stop_pct     = float(risk.get("max_stop_pct", 4.5)) / 100
        self.trail            = bool( risk.get("use_trailing",         True))
        self.trail_step       = float(risk.get("trailing_step_pct",    0.7)) / 100
        self.min_hold         = int(  risk.get("min_hold_minutes",     30))  * 60
        self.risk_per_trade   = float(risk.get("risk_per_trade_pct",   1.0)) / 100
        self.min_profit_close = float(risk.get("min_profit_close_pct", 3.0)) / 100

        self.score_long_open  = float(thr.get("score_long_open",  85))
        self.score_short_open = float(thr.get("score_short_open", 15))
        self.score_close      = float(thr.get("score_close",      50))

        self.max_trades_day   = int(  lim.get("max_trades_per_day", 10))
        self.max_open_pos     = int(  lim.get("max_open_positions",  5))
        self.daily_target_pct = float(lim.get("daily_target_pct",  8.0)) / 100
        self.max_hold_sec     = int(  lim.get("max_hold_hours",       48)) * 3600
        self.daily_loss_limit = float(lim.get("daily_loss_limit_pct",  5.0)) / 100

        self.vol_mult         = float(misc.get("volume_burst_multiplier", 2.0))
        self.min_notional     = float(misc.get("min_notional_usdt", 30000.0))
        self.pyramid_enabled  = bool( pyr.get("enabled",    False))

        # ── Çoklu Timeframe Ayarları ──────────────────────────────
        self.mtf_enabled      = bool( mtf.get("enabled",          True))
        # HTF'de LONG için minimum skor (yön konfirmasyonu)
        self.mtf_long_min     = float(mtf.get("htf_long_min",     55.0))
        # HTF'de SHORT için maksimum skor (yön konfirmasyonu)
        self.mtf_short_max    = float(mtf.get("htf_short_max",    45.0))

        self.lock             = threading.Lock()
        self._stopped         = False
        self.on_event         = self.cfg.get("on_event")

        # ── LTF (low timeframe — örn. 5m) serileri ───────────────
        self.close_series     = {s: deque(maxlen=2048) for s in symbols}
        self.high_series      = {s: deque(maxlen=2048) for s in symbols}
        self.low_series       = {s: deque(maxlen=2048) for s in symbols}
        self.vol_series       = {s: deque(maxlen=2048) for s in symbols}
        self.last_close_time  = {s: 0 for s in symbols}

        # ── HTF (higher timeframe — örn. 1h) serileri ────────────
        self.htf_close_series = {s: deque(maxlen=500) for s in symbols}
        self.htf_high_series  = {s: deque(maxlen=500) for s in symbols}
        self.htf_low_series   = {s: deque(maxlen=500) for s in symbols}
        self.htf_vol_series   = {s: deque(maxlen=500) for s in symbols}
        self.htf_last_time    = {s: 0 for s in symbols}

        self.open_positions    = {}
        self.trade_count_today = 0
        self.pnl_total_usd     = 0.0
        self.daily_pnl_usd     = 0.0
        self._daily_fired      = False
        self.last_reset_day    = time.strftime("%Y-%m-%d")

        # ── Piyasa Rejimi + Sembol Yöneticisi ────────────────────
        self.regime  = MarketRegimeDetector(cfg)
        self.sym_mgr = SymbolManager(cfg, starting_equity=self.equity)

        # ── Kara Liste ────────────────────────────────────────────
        self.blacklist: dict[str, float] = {}

        self.csv_path          = self.data_dir / "trade_logs.csv"
        self.events_path       = self.data_dir / "engine_events.log"
        self.allowed_symbol    = None

    # ──────────────────────────────────────────────────────────────
    # Durdurma
    # ──────────────────────────────────────────────────────────────
    def stop(self):
        with self.lock:
            self._stopped = True
        self._fire("ENGINE_STOP")
        log_info("Engine durduruldu")

    # ──────────────────────────────────────────────────────────────
    # Event Yayıncısı
    # ──────────────────────────────────────────────────────────────
    def _fire(self, etype: str, **kw):
        ts   = int(time.time())
        line = f"{ts}\t{etype}\t" + " ".join(f"{k}={v}" for k, v in kw.items())
        try:
            with open(self.events_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        log_event(etype, **kw)
        if callable(self.on_event):
            try:
                self.on_event(etype, kw)
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────────
    # Kara Liste
    # ──────────────────────────────────────────────────────────────
    def add_to_blacklist(self, symbol: str, hours: float = 24.0):
        with self.lock:
            self.blacklist[symbol] = time.time() + hours * 3600
        self._fire("BLACKLIST_ADD", symbol=symbol, hours=hours)
        log_info(f"Kara listeye eklendi: {symbol} ({hours}sa)")

    def remove_from_blacklist(self, symbol: str):
        with self.lock:
            self.blacklist.pop(symbol, None)
        log_info(f"Kara listeden çıkarıldı: {symbol}")

    def get_blacklist(self) -> list:
        with self.lock:
            now     = time.time()
            expired = [s for s, exp in self.blacklist.items() if now >= exp]
            for s in expired:
                del self.blacklist[s]
            return [(s, round((exp - now) / 3600, 1))
                    for s, exp in self.blacklist.items()]

    # ──────────────────────────────────────────────────────────────
    # Veri Besleme — LTF
    # ──────────────────────────────────────────────────────────────
    def seed_from_candles(self, symbol: str, candles: list):
        with self.lock:
            d = self.close_series.setdefault(symbol, deque(maxlen=2048))
            h = self.high_series.setdefault( symbol, deque(maxlen=2048))
            l = self.low_series.setdefault(  symbol, deque(maxlen=2048))
            v = self.vol_series.setdefault(  symbol, deque(maxlen=2048))
            for c in candles:
                d.append(float(c.get("close",  0)))
                h.append(float(c.get("high",   0)))
                l.append(float(c.get("low",    0)))
                v.append(float(c.get("volume", 0)))
            if candles:
                self.last_close_time[symbol] = int(candles[-1].get("close_time", 0))

    def on_candle(self, symbol: str, candle: dict):
        with self.lock:
            if self._stopped:
                return
            self._reset_daily_if_needed()
            price  = float(candle.get("close",  0))
            high   = float(candle.get("high",   0))
            low    = float(candle.get("low",    0))
            volume = float(candle.get("volume", 0))
            self.close_series[symbol].append(price)
            self.high_series[symbol].append(high)
            self.low_series[symbol].append(low)
            self.vol_series[symbol].append(volume)
            self.last_close_time[symbol] = int(candle.get("close_time", 0))
            prices  = list(self.close_series[symbol])
            highs   = list(self.high_series[symbol])
            lows    = list(self.low_series[symbol])
            volumes = list(self.vol_series[symbol])
            in_pos  = symbol in self.open_positions

        # ── Lock DIŞINDA CPU-yoğun hesaplama ─────────────────
        if len(prices) < 50:
            return

        news_score = get_sentiment_score()
        result     = score_symbol(prices, highs, lows, volumes, news_score)
        score      = result["final_score"]

        # ── Sonuçla birlikte tekrar lock al ──────────────────
        with self.lock:
            if self._stopped:
                return
            if in_pos:
                self._manage(symbol, price, score)
            else:
                self._try_open(symbol, price, score, prices, volumes, result)

    # ──────────────────────────────────────────────────────────────
    # Veri Besleme — HTF
    # ──────────────────────────────────────────────────────────────
    def seed_from_candles_htf(self, symbol: str, candles: list):
        """1h (veya başka HTF) geçmiş mumlarını yükler."""
        with self.lock:
            pd = self.htf_close_series.setdefault(symbol, deque(maxlen=500))
            hd = self.htf_high_series.setdefault( symbol, deque(maxlen=500))
            ld = self.htf_low_series.setdefault(  symbol, deque(maxlen=500))
            vd = self.htf_vol_series.setdefault(  symbol, deque(maxlen=500))
            for c in candles:
                pd.append(float(c.get("close",  0)))
                hd.append(float(c.get("high",   0)))
                ld.append(float(c.get("low",    0)))
                vd.append(float(c.get("volume", 0)))
            if candles:
                self.htf_last_time[symbol] = int(candles[-1].get("close_time", 0))

    def on_candle_htf(self, symbol: str, candle: dict):
        """1h mumunu HTF serilerine ekler (lock dışından çağrılır)."""
        with self.lock:
            if self._stopped:
                return
            self.htf_close_series[symbol].append(float(candle.get("close",  0)))
            self.htf_high_series[symbol].append( float(candle.get("high",   0)))
            self.htf_low_series[symbol].append(  float(candle.get("low",    0)))
            self.htf_vol_series[symbol].append(  float(candle.get("volume", 0)))

    # ──────────────────────────────────────────────────────────────
    # Günlük Sıfırlama
    # ──────────────────────────────────────────────────────────────
    def _reset_daily_if_needed(self):
        today = time.strftime("%Y-%m-%d")
        if today != self.last_reset_day:
            self.trade_count_today = 0
            self.daily_pnl_usd     = 0.0
            self._daily_fired      = False
            self.last_reset_day    = today
            log_info("Günlük sayaçlar sıfırlandı")

    # ──────────────────────────────────────────────────────────────
    # Pozisyon Büyüklüğü
    # ──────────────────────────────────────────────────────────────
    def _lot(self, price: float, dynamic_sl_pct: float = None) -> float:
        if dynamic_sl_pct is None:
            dynamic_sl_pct = self.sl_pct
        current_equity = self.equity + self.pnl_total_usd
        risk_usdt      = current_equity * self.risk_per_trade
        denom          = price * max(dynamic_sl_pct, 0.001)
        return max(0.0001, risk_usdt / denom)

    # ──────────────────────────────────────────────────────────────
    # Günlük Limit Kontrolleri
    # ──────────────────────────────────────────────────────────────
    def _daily_target_hit(self) -> bool:
        if self._daily_fired:
            return True
        target = (self.equity + self.pnl_total_usd) * self.daily_target_pct
        if self.daily_pnl_usd >= target:
            self._daily_fired = True
            self._fire("DAILY_TARGET_HIT",
                       daily_pnl=round(self.daily_pnl_usd, 2),
                       target=round(target, 2))
            log_info(f"Günlük hedef ulaşıldı: ${self.daily_pnl_usd:.2f}")
            return True
        return False

    def _daily_loss_hit(self) -> bool:
        limit = (self.equity + self.pnl_total_usd) * self.daily_loss_limit
        if self.daily_pnl_usd <= -limit:
            self._fire("DAILY_LOSS_LIMIT",
                       daily_pnl=round(self.daily_pnl_usd, 2),
                       limit=round(-limit, 2))
            log_info(f"Günlük zarar limitine ulaşıldı: ${self.daily_pnl_usd:.2f}")
            return True
        return False

    # ──────────────────────────────────────────────────────────────
    # HTF Skor Hesapla
    # ──────────────────────────────────────────────────────────────
    def _htf_score(self, symbol: str) -> float:
        """
        1h serisinden skor hesaplar.
        Yeterli veri yoksa 50.0 (nötr) döner — bu durumda
        MTF filtresi engel olmaz (fail-open davranışı).
        """
        prices  = list(self.htf_close_series.get(symbol, []))
        highs   = list(self.htf_high_series.get( symbol, []))
        lows    = list(self.htf_low_series.get(  symbol, []))
        volumes = list(self.htf_vol_series.get(  symbol, []))
        if len(prices) < 50:
            return 50.0
        try:
            result = score_symbol(prices, highs, lows, volumes)
            return result["final_score"]
        except Exception:
            return 50.0

    # ──────────────────────────────────────────────────────────────
    # Ana İşlem Akışı
    # ──────────────────────────────────────────────────────────────


    # ── Pozisyon Yönetimi ─────────────────────────────────────────
    def _manage(self, symbol: str, price: float, score: float):
        pos    = self.open_positions[symbol]
        age    = time.time() - pos["ts_open"]
        mult   = 1 if pos["side"] == "LONG" else -1
        change = (price - pos["entry"]) / pos["entry"] * mult

        pos_sl_pct = pos.get("sl_pct", self.sl_pct)
        if change <= -pos_sl_pct:
            self._close(symbol, price, change, "SL")
            return

        if self.trail and change > 0:
            # Pozisyona özgü trail_step kullan (açılışta rejime göre belirlendi)
            pos_trail    = pos.get("trail_step", self.trail_step)
            trail_locked = pos.get("trail_locked", None)
            if trail_locked is None or change > trail_locked + pos_trail:
                pos["trail_locked"] = change
                self._fire("TRAIL_LOCK", symbol=symbol,
                           locked=f"{change*100:.2f}%")

        if age < self.min_hold:
            return

        if age >= self.max_hold_sec:
            self._close(symbol, price, change, "MaxHold")
            return

        reason = self._exit_reason(pos, price, change, score)
        if reason:
            self._close(symbol, price, change, reason)

    def _exit_reason(self, pos: dict, price: float, change: float, score: float):
        pos_sl_pct = pos.get("sl_pct", self.sl_pct)
        if change <= -pos_sl_pct:
            return "SL"
        if change >= self.tp_pct and change >= self.min_profit_close:
            return "TP"
        if change >= self.min_profit_close:
            if pos["side"] == "LONG"  and score < self.score_close:
                return "ScoreClose"
            if pos["side"] == "SHORT" and score > self.score_close:
                return "ScoreClose"
            locked = pos.get("trail_locked", change)
            # Pozisyona özgü trail_step (açılışta rejime göre belirlendi)
            pos_trail = pos.get("trail_step", self.trail_step)
            if self.trail and change < locked - pos_trail:
                return "Trail"
        return None

    # ── Pozisyon Açma (v6: HTF konfirmasyon eklendi) ─────────────
    def _try_open(self, symbol: str, price: float, score: float,
                  prices: list, volumes: list, result: dict):
        # Temel kontrol kapıları
        if self._stopped:                                         return
        if len(self.open_positions) >= self.max_open_pos:         return
        if self.trade_count_today  >= self.max_trades_day:        return
        if self.allowed_symbol and symbol != self.allowed_symbol: return
        if self._daily_target_hit():                              return
        if self._daily_loss_hit():                                return

        # Kara liste kontrolü
        bl_exp = self.blacklist.get(symbol)
        if bl_exp is not None:
            if time.time() < bl_exp:
                return
            else:
                del self.blacklist[symbol]

        # Minimum işlem hacmi filtresi
        if len(prices) >= 20:
            avg_notional = (sum(prices[-20:]) / 20) * (sum(volumes[-20:]) / 20)
            if avg_notional < self.min_notional:
                return

        # Hacim patlaması filtresi
        if len(volumes) >= 20:
            rv = sum(volumes[-3:]) / 3
            hv = sum(volumes[-20:-3]) / 17
            if hv > 0 and rv < hv * self.vol_mult:
                self._fire("OPEN_BLOCK", cause="LOW_VOLUME", symbol=symbol)
                return

        # Portföy yönü filtresi
        btc = self.open_positions.get("BTCUSDT")
        if btc:
            if btc["side"] == "SHORT" and score >= self.score_long_open:
                self._fire("OPEN_BLOCK", cause="BTC_SHORT_BLOCKS_LONG", symbol=symbol)
                return
            if btc["side"] == "LONG" and score <= self.score_short_open:
                if score > self.score_short_open / 2:
                    self._fire("OPEN_BLOCK", cause="BTC_LONG_WEAK_SHORT", symbol=symbol)
                    return

        # Makro sentiment filtresi
        sentiment = get_market_sentiment()
        # Adaptif giriş eşiği: KONSOL'de +7, BEARISH'de +99 (pratikte giriş yok)
        _adsl_thr = adaptive_sl.compute(
            regime               = self.regime._last_regime,
            atr_pct              = result.get("components", {}).get("atr_pct", 0.0),
            base_score_threshold = self.score_long_open,
            base_atr_multiplier  = self.atr_multiplier,
            base_trail_step      = self.trail_step,
            cfg                  = self.cfg,
        )
        effective_long_thr = _adsl_thr["score_threshold"]
        side      = None
        if score >= effective_long_thr and sentiment != "BEARISH":
            side = "LONG"
        elif score <= self.score_short_open and sentiment != "BULLISH":
            side = "SHORT"

        if not side:
            return
        adx_val = result.get("components", {}).get("adx", 0.0)
        if self.adx_filter_enabled and adx_val > 0 and adx_val < self.adx_filter_threshold:
            return

        # ── ATR Minimum Filtresi ───────────────────────────────
        if self.atr_filter_enabled:
            atr_val = result.get("components", {}).get("atr_pct", 0.0)
            if atr_val < self.atr_filter_min:
                self._fire("OPEN_BLOCK", cause="ATR_TOO_LOW",
                           symbol=symbol, atr=round(atr_val, 3))
                return

        # ── RSI Filtresi ───────────────────────────────────────
        if self.rsi_filter_enabled:
            rsi_val = result.get("components", {}).get("rsi", 50.0)
            if side == "LONG"  and rsi_val > self.rsi_max_long:
                self._fire("OPEN_BLOCK", cause="RSI_TOO_HIGH",
                           symbol=symbol, rsi=round(rsi_val, 1))
                return
            if side == "SHORT" and rsi_val < self.rsi_min_short:
                self._fire("OPEN_BLOCK", cause="RSI_TOO_LOW",
                           symbol=symbol, rsi=round(rsi_val, 1))
                return

        # ── Çoklu Timeframe Konfirmasyon ─────────────────────────
        if self.mtf_enabled:
            htf_sc = self._htf_score(symbol)
            if side == "LONG"  and htf_sc < self.mtf_long_min:
                self._fire("OPEN_BLOCK", cause="MTF_NO_CONFIRM_LONG",
                           symbol=symbol, htf_score=round(htf_sc, 1))
                return
            if side == "SHORT" and htf_sc > self.mtf_short_max:
                self._fire("OPEN_BLOCK", cause="MTF_NO_CONFIRM_SHORT",
                           symbol=symbol, htf_score=round(htf_sc, 1))
                return

        # Sembol performans filtresi
        sym_mult = self.sym_mgr.size_multiplier(symbol)
        if sym_mult == 0:
            return  # tamamen baskılanmış sembol

        self._open(symbol, price, side, result, size_mult=sym_mult)

    # ──────────────────────────────────────────────────────────────
    # Pozisyon Aç / Kapat
    # ──────────────────────────────────────────────────────────────
    def _open(self, symbol: str, price: float, side: str, result: dict, size_mult: float = 1.0):
        # Adaptif SL: ATR × rejime özgü çarpan
        atr_pct_val = result.get("components", {}).get("atr_pct", 0.0)
        regime      = self.regime._last_regime
        adsl = adaptive_sl.compute(
            regime               = regime,
            atr_pct              = atr_pct_val,
            base_score_threshold = self.score_long_open,
            base_atr_multiplier  = self.atr_multiplier,
            base_trail_step      = self.trail_step,
            cfg                  = self.cfg,
        )
        if self.use_atr_stop and atr_pct_val > 0:
            final_sl_pct = adsl["sl_pct"]
        else:
            final_sl_pct = self.sl_pct
        pos_trail_step = adsl["trail_step"]

        qty = self._lot(price, dynamic_sl_pct=final_sl_pct) * size_mult
        self.open_positions[symbol] = {
            "side":       side,
            "entry":      price,
            "qty":        qty,
            "ts_open":    time.time(),
            "sl_pct":     final_sl_pct,
            "trail_step": pos_trail_step,   # rejime göre belirlendi
        }
        self.trade_count_today += 1
        self._log_trade(symbol, side, qty, price, "", 0.0, 0.0,
                        f"OPEN sl_pct={final_sl_pct*100:.2f}% regime={regime} score={result['final_score']}")
        self._fire("OPEN", symbol=symbol, side=side,
                   entry=price, score=result["final_score"], regime=regime)

    def _close(self, symbol: str, price: float, change_pct: float, reason: str):
        pos = self.open_positions.pop(symbol, None)
        if not pos:
            return
        entry   = pos["entry"]
        qty     = pos["qty"]
        pnl_usd = ((price - entry) if pos["side"] == "LONG"
                   else (entry - price)) * qty

        self.pnl_total_usd += pnl_usd
        self.daily_pnl_usd += pnl_usd
        self.sym_mgr.record_trade(symbol, pnl_usd)
        self.sym_mgr.update_equity(self.equity + self.pnl_total_usd)

        self._log_trade(symbol, pos["side"], qty, entry, price,
                        round(change_pct * 100, 3), round(pnl_usd, 3), reason)
        self._fire("EXIT", symbol=symbol, side=pos["side"], reason=reason,
                   pnl_usd=round(pnl_usd, 2),
                   pnl_pct=f"{change_pct*100:.2f}%")

    # ──────────────────────────────────────────────────────────────
    # CSV Log
    # ──────────────────────────────────────────────────────────────
    def _log_trade(self, sym, side, qty, entry, exitp,
                   kar_pct, kar_usd, note):
        ts  = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        new = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        try:
            with open(self.csv_path, "a", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";")
                if new:
                    w.writerow(["Tarih","Sembol","Yon","GirisFiyati",
                                "CikisFiyati","KarYuzde","KarUSD","Not"])
                w.writerow([ts, sym, side, entry or "",
                            exitp or "", kar_pct, kar_usd, note])
        except Exception as e:
            log_error(f"CSV yazma hatası: {e}")

    # ──────────────────────────────────────────────────────────────
    # GUI'ye Veri
    # ──────────────────────────────────────────────────────────────
    def get_open_positions(self) -> list:
        with self.lock:
            out = []
            for sym, p in self.open_positions.items():
                age = int(time.time() - p["ts_open"])
                out.append({
                    "symbol":    sym,
                    "side":      p["side"],
                    "entry":     p["entry"],
                    "age_min":   round(age / 60, 1),
                    "trail_pct": round(p.get("trail_locked", 0.0) * 100, 2),
                })
            return out

    def get_pnl(self) -> dict:
        with self.lock:
            base = self.equity
            return {
                "usd":       round(self.pnl_total_usd, 2),
                "pct":       round(self.pnl_total_usd / base * 100, 3) if base else 0.0,
                "daily_usd": round(self.daily_pnl_usd, 2),
                "equity":    round(base + self.pnl_total_usd, 2),
            }

    def set_allowed_symbol(self, sym_or_none):
        with self.lock:
            self.allowed_symbol = sym_or_none
