# market_regime.py — BTC Piyasa Rejimi Motoru (Modül 1)
#
# Walk-forward analizi net gösterdi: strateji YALNIZCA BTC trend döneminde
# çalışıyor. Temmuz-Eylül 2025 (BTC yükseliş): +$2,228. Diğer 9 ay: -$1,800.
#
# Bu modül BTC'nin anlık rejimini 3 katmanda ölçer:
#   Katman 1 — Makro trend    : EMA20 > EMA50 > EMA200?
#   Katman 2 — Volatilite     : ATR ort. seviyede mi, kaotik mi?
#   Katman 3 — Hacim momentum : Son mumlar hacimli mi?
#
# Çıktı: "TREND" | "KONSOL" | "BEARISH"
#   TREND   → tam işlem (size_multiplier=1.0)
#   KONSOL  → küçük pozisyon (size_multiplier=0.5)
#   BEARISH → işlem yok (size_multiplier=0.0)
#
# Hem backtest.py hem engine.py tarafından kullanılır.
# BTC fiyat geçmişi dışarıdan verilir (btc_closes buffer'ı).

import numpy as np
import pandas as pd


class MarketRegimeDetector:
    """
    BTC fiyat serisinden anlık piyasa rejimini tespit eder.

    config (market_regime bloğu):
      enabled          : modül açık/kapalı
      ema_fast         : kısa EMA periyodu (örn 20)
      ema_slow         : orta EMA periyodu (örn 50)
      ema_long         : uzun EMA periyodu (örn 200)
      atr_period       : ATR periyodu (örn 14)
      atr_mult_thresh  : ATR ortalamasının kaç katına çıkınca "volatil" sayılır (örn 1.8)
      vol_window       : hacim karşılaştırma penceresi (örn 20)
      vol_burst_mult   : son mum hacmi ortalamanın kaç katını aşarsa "hacimli" (örn 1.5)
      min_candles      : karar için minimum mum sayısı (örn 220)
    """

    TREND   = "TREND"
    KONSOL  = "KONSOL"
    BEARISH = "BEARISH"

    # Rejime göre pozisyon boyutu çarpanı
    SIZE = {TREND: 1.0, KONSOL: 0.5, BEARISH: 0.0}

    def __init__(self, cfg: dict):
        r = cfg.get("market_regime", {})
        self.enabled         = bool( r.get("enabled",         True))
        self.ema_fast        = int(  r.get("ema_fast",          20))
        self.ema_slow        = int(  r.get("ema_slow",          50))
        self.ema_long        = int(  r.get("ema_long",         200))
        self.atr_period      = int(  r.get("atr_period",        14))
        self.atr_mult_thresh = float(r.get("atr_mult_thresh",  1.8))
        self.vol_window      = int(  r.get("vol_window",        20))
        self.vol_burst_mult  = float(r.get("vol_burst_mult",   1.5))
        self.min_candles     = int(  r.get("min_candles",      210))

        self._last_regime    = self.KONSOL
        self._last_detail    = {}

    def detect(self, btc_closes: list,
               btc_highs: list  = None,
               btc_lows:  list  = None,
               btc_vols:  list  = None) -> str:
        """
        BTC fiyat serisini analiz eder, rejim döner.
        Yetersiz veri varsa son bilinen rejimi korur.
        """
        if not self.enabled:
            return self.TREND

        if len(btc_closes) < self.min_candles:
            return self._last_regime

        arr  = np.array(btc_closes, dtype=float)
        s    = pd.Series(arr)
        price = arr[-1]

        # ── Katman 1: EMA trend ──────────────────────────────
        ema_f = float(s.ewm(span=self.ema_fast, adjust=False).mean().iloc[-1])
        ema_s = float(s.ewm(span=self.ema_slow, adjust=False).mean().iloc[-1])
        ema_l = float(s.ewm(span=self.ema_long, adjust=False).mean().iloc[-1])

        # Tam trend: fiyat > EMA20 > EMA50 > EMA200
        full_trend   = price > ema_f > ema_s > ema_l
        # Zayıf trend: en az fiyat > EMA50 > EMA200
        weak_trend   = price > ema_s > ema_l
        # Düşüş: fiyat < EMA200
        bearish_ema  = price < ema_l

        # ── Katman 2: ATR volatilite ─────────────────────────
        atr_chaotic   = False
        atr_percentile = 50.0  # varsayılan
        if btc_highs and btc_lows and len(btc_highs) >= self.atr_period + 1:
            h = np.array(btc_highs[-self.atr_period*3:], dtype=float)
            l = np.array(btc_lows[-self.atr_period*3:],  dtype=float)
            c = arr[-self.atr_period*3:]
            trs = []
            for i in range(1, len(c)):
                tr = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
                trs.append(tr)
            if len(trs) >= self.atr_period * 2:
                recent_atr = float(np.mean(trs[-self.atr_period:]))
                base_atr   = float(np.mean(trs[-self.atr_period*2:-self.atr_period]))
                if base_atr > 0:
                    atr_chaotic = recent_atr / base_atr > self.atr_mult_thresh
                # ATR percentile — son 100 TR içindeki sırası
                if len(trs) >= 20:
                    window = trs[-100:] if len(trs) >= 100 else trs
                    atr_percentile = float(
                        sum(1 for x in window if x <= recent_atr) / len(window) * 100
                    )
        # ── Katman 3: Hacim momentum ─────────────────────────
        vol_strong = False
        if btc_vols and len(btc_vols) >= self.vol_window + 3:
            recent_vol = float(np.mean(btc_vols[-3:]))
            base_vol   = float(np.mean(btc_vols[-self.vol_window:-3]))
            if base_vol > 0:
                vol_strong = recent_vol / base_vol >= self.vol_burst_mult

        # ── Rejim kararı ─────────────────────────────────────
        # BEARISH: fiyat EMA200 altında VEYA çok düşük volatilite (sıkışma)
        if bearish_ema:
            regime = self.BEARISH
        # EMA50 altına düşüş de BEARISH sayılır
        elif atr_percentile < 20:
            regime = self.KONSOL

        # TREND: tam ema sırası + yüksek ATR değil + hacim destekliyor
        elif full_trend and not atr_chaotic:
            regime = self.TREND

        # TREND: tam ema sırası + hacim güçlü (ATR kaotik olsa bile)
        elif full_trend and vol_strong:
            regime = self.TREND

        # KONSOL: zayıf trend veya belirsiz durum
        elif weak_trend:
            regime = self.KONSOL

        # KONSOL: ne tam trend ne bearish
        else:
            regime = self.KONSOL

        self._last_regime = regime
        self._last_detail = {
            "regime":       regime,
            "price":        round(price, 2),
            "ema_fast":     round(ema_f, 2),
            "ema_slow":     round(ema_s, 2),
            "ema_long":     round(ema_l, 2),
            "full_trend":   full_trend,
            "weak_trend":   weak_trend,
            "bearish_ema":  bearish_ema,
            "atr_chaotic":  atr_chaotic,
            "vol_strong":   vol_strong,
            "atr_percentile": round(atr_percentile, 1),
        }
        return regime

    def size_multiplier(self) -> float:
        """Mevcut rejime göre pozisyon boyutu çarpanı döner."""
        return self.SIZE.get(self._last_regime, 0.5)

    def is_open(self) -> bool:
        """İşlem açılabilir mi?"""
        return self._last_regime in (self.TREND, self.KONSOL)

    def detail(self) -> dict:
        """Son tespitin detayları (log/rapor için)."""
        return self._last_detail.copy()

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "regime":  self._last_regime,
            "size_multiplier": self.size_multiplier(),
        }
