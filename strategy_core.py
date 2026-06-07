# strategy_core.py — Teknik analiz ve skorlama motoru
# ──────────────────────────────────────────────────────────────
# pandas-ta ENTEGRASYONU (Adım 1)  —  v2 (pandas-ta 0.4.x uyumlu)
# ──────────────────────────────────────────────────────────────
# pandas-ta 0.4.x; numpy 2.x + numba ile hızlandırılmış indikatörler.
# pandas-ta kurulu DEĞİLSE veya herhangi bir hata olursa otomatik
# olarak yerleşik (builtin) implementasyonlara geri düşülür.
#
# Aktif motor: strategy_core.INDICATOR_ENGINE  →  "pandas_ta" | "builtin"
# İlk çalıştırmada numba JIT derleme yapar (~10s), sonraki çağrılar hızlıdır.
# ──────────────────────────────────────────────────────────────
import numpy as np
import pandas as pd
from scipy.signal import find_peaks

try:
    import pandas_ta as ta
    _PANDAS_TA = True
    INDICATOR_ENGINE = "pandas_ta"
except Exception:
    _PANDAS_TA = False
    INDICATOR_ENGINE = "builtin"


# ─── Yardımcı: sütun adına göre güvenli değer okuma ─────────────
def _col(df: pd.DataFrame, keyword: str, col_idx: int) -> float:
    """
    DataFrame'den önce 'keyword' içeren sütun adı arar, bulamazsa
    col_idx pozisyonunu kullanır. Böylece sürümler arası sütun adı
    değişikliklerine karşı dayanıklı olur.
    """
    matches = [c for c in df.columns if keyword.upper() in c.upper()]
    col = matches[0] if matches else df.columns[col_idx]
    val = float(df[col].iloc[-1])
    return val if not np.isnan(val) else float("nan")


# ──────────────────────────────────────────────
# İndikatör Hesaplayıcılar  (pandas-ta öncelikli + builtin fallback)
# ──────────────────────────────────────────────

def _rsi(prices: np.ndarray, period: int = 14) -> pd.Series:
    """Wilder RSI. Öncelik: pandas-ta  →  builtin EWM."""
    if _PANDAS_TA:
        try:
            r = ta.rsi(pd.Series(prices, dtype=float), length=period)
            if r is not None and len(r) == len(prices):
                return r.fillna(50)
        except Exception:
            pass
    s     = pd.Series(prices, dtype=float)
    delta = s.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    ag    = gain.ewm(com=period - 1, min_periods=period).mean()
    al    = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = ag / al.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _macd(prices: np.ndarray,
          fast: int = 12, slow: int = 26, sig: int = 9):
    """MACD line, signal, histogram. Sütunlar: MACD | MACDh | MACDs."""
    if len(prices) < slow:
        return 0.0, 0.0, 0.0
    if _PANDAS_TA:
        try:
            df = ta.macd(pd.Series(prices, dtype=float),
                         fast=fast, slow=slow, signal=sig)
            if df is not None and len(df.columns) >= 3:
                # Sütun adlarını keyword ile kesin eşleştir
                macd_cols  = [c for c in df.columns if "MACD_"  in c and "MACDh_" not in c and "MACDs_" not in c]
                hist_cols  = [c for c in df.columns if "MACDh_" in c]
                sig_cols   = [c for c in df.columns if "MACDs_" in c]
                if macd_cols and hist_cols and sig_cols:
                    ml   = float(df[macd_cols[0]].iloc[-1])
                    hist = float(df[hist_cols[0]].iloc[-1])
                    sl_  = float(df[sig_cols[0]].iloc[-1])
                    if not any(np.isnan(v) for v in (ml, hist, sl_)):
                        return ml, sl_, hist
        except Exception:
            pass
    s   = pd.Series(prices, dtype=float)
    ml  = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    sl_ = ml.ewm(span=sig, adjust=False).mean()
    return float(ml.iloc[-1]), float(sl_.iloc[-1]), float((ml - sl_).iloc[-1])


def _bollinger(prices: np.ndarray, period: int = 20, std_dev: float = 2.0):
    """Bollinger Bantları: üst, orta, alt. Sütunlar: BBL | BBM | BBU."""
    if len(prices) < period:
        return 0.0, 0.0, 0.0
    if _PANDAS_TA:
        try:
            df = ta.bbands(pd.Series(prices, dtype=float),
                           length=period, std=std_dev)
            if df is not None and len(df.columns) >= 3:
                lower = _col(df, "BBL_", 0)
                mid   = _col(df, "BBM_", 1)
                upper = _col(df, "BBU_", 2)
                if not any(np.isnan(v) for v in (lower, mid, upper)):
                    return upper, mid, lower
        except Exception:
            pass
    s   = pd.Series(prices, dtype=float)
    mid = s.rolling(period).mean()
    std = s.rolling(period).std(ddof=1)
    return float((mid + std * std_dev).iloc[-1]), \
           float(mid.iloc[-1]), \
           float((mid - std * std_dev).iloc[-1])


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
         period: int = 14) -> float:
    """ATR — SMA tabanlı (orijinal davranışla eşleşir)."""
    if len(closes) < period + 1:
        return 0.0
    if _PANDAS_TA:
        try:
            a = ta.atr(pd.Series(highs,  dtype=float),
                       pd.Series(lows,   dtype=float),
                       pd.Series(closes, dtype=float),
                       length=period, mamode="sma")
            if a is not None and len(a) > 0:
                val = float(a.iloc[-1])
                if not np.isnan(val):
                    return val
        except Exception:
            pass
    h, l, c = highs[-period-1:], lows[-period-1:], closes[-period-1:]
    prev_c  = c[:-1]
    tr = np.maximum(h[1:]-l[1:],
                    np.maximum(np.abs(h[1:]-prev_c), np.abs(l[1:]-prev_c)))
    return float(np.mean(tr))


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
         period: int = 14) -> float:
    """
    ADX — trend gücü (0-100). Sütun: ADX_{period}.
    Scorlamayı etkilemez; components içinde bilgi amaçlı raporlanır.
    Yorumlama: <20 = yatay, 20-40 = orta trend, >40 = güçlü trend.
    """
    if not _PANDAS_TA or len(closes) < period * 2:
        return 0.0
    try:
        df = ta.adx(pd.Series(highs,  dtype=float),
                    pd.Series(lows,   dtype=float),
                    pd.Series(closes, dtype=float),
                    length=period)
        if df is not None and len(df.columns) >= 1:
            val = _col(df, "ADX_", 0)
            return 0.0 if np.isnan(val) else val
    except Exception:
        pass
    return 0.0


def _trend_structure(prices: list, window: int = 20) -> float:
    """HH+HL → 100 (yükselen), LL+LH → 0 (düşen), diğer → 50."""
    if len(prices) < window:
        return 50.0
    p     = prices[-window:]
    highs = [max(p[i:i+3]) for i in range(0, len(p)-2, 2)]
    lows  = [min(p[i:i+3]) for i in range(0, len(p)-2, 2)]
    if len(highs) < 3 or len(lows) < 3:
        return 50.0
    if highs[-1] > highs[-2] > highs[-3] and lows[-1] > lows[-2] > lows[-3]:
        return 100.0
    if lows[-1] < lows[-2] < lows[-3] and highs[-1] < highs[-2] < highs[-3]:
        return 0.0
    return 50.0


def _bollinger_score(price: float, upper: float, lower: float) -> float:
    """Fiyatın BB içindeki konumunu sürekli 0-100 skoruna çevirir."""
    if upper <= lower:
        return 50.0
    return float(np.clip((price - lower) / (upper - lower) * 100, 0, 100))


def _rsi_divergence(prices: np.ndarray, rsi_arr: np.ndarray,
                    lookback: int = 30) -> int:
    """Pozitif uyuşmazlık → +10, negatif → -10, yok → 0."""
    if len(prices) < lookback:
        return 0
    ps = prices[-lookback:]
    rs = rsi_arr[-lookback:]
    lows,  _ = find_peaks(-ps, distance=5)
    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        if ps[i2] < ps[i1] and rs[i2] > rs[i1]:
            return 10
    highs, _ = find_peaks(ps, distance=5)
    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        if ps[i2] > ps[i1] and rs[i2] < rs[i1]:
            return -10
    return 0


def _patterns(prices: np.ndarray, lookback: int = 40) -> int:
    """Çift Dip → +10, Çift Tepe → -10, yok → 0."""
    if len(prices) < lookback:
        return 0
    seg = prices[-lookback:]
    boost = 0
    peaks,   _ = find_peaks( seg, distance=5)
    troughs, _ = find_peaks(-seg, distance=5)
    if len(troughs) >= 2:
        t1, t2 = troughs[-2], troughs[-1]
        v1, v2 = seg[t1], seg[t2]
        mids = [p for p in peaks if t1 < p < t2]
        if mids and abs(v2 - v1) / (v1 + 1e-9) < 0.03:
            neck = seg[mids[0]]
            if neck > v2 and seg[-1] > neck:
                boost += 10
    if len(peaks) >= 2:
        p1, p2 = peaks[-2], peaks[-1]
        v1, v2 = seg[p1], seg[p2]
        mids = [t for t in troughs if p1 < t < p2]
        if mids and abs(v2 - v1) / (v1 + 1e-9) < 0.03:
            neck = seg[mids[0]]
            if neck < v2 and seg[-1] < neck:
                boost -= 10
    return boost


# ──────────────────────────────────────────────
# Ana Skorlama Fonksiyonu
# ──────────────────────────────────────────────

def score_symbol(prices: list, highs: list = None, lows: list = None,
                 volumes: list = None, news_score: float = 50.0) -> dict:
    """
    Tüm indikatörleri birleştirip 0-100 arası bir final skor üretir.
    Dönüş değeri engine.py / backtest.py ile birebir uyumludur:
        {
          "final_score": float,
          "is_trending": bool,
          "components":  dict   ← "adx" ve "engine" eklendi
        }
    """
    if not prices or len(prices) < 50:
        return {"final_score": 50.0, "is_trending": False, "components": {}}

    arr   = np.array(prices, dtype=float)
    h_arr = np.array(highs,  dtype=float) if highs and len(highs) == len(prices) else arr
    l_arr = np.array(lows,   dtype=float) if lows  and len(lows)  == len(prices) else arr
    price = arr[-1]

    # 1. Volatilite filtresi
    atr     = _atr(h_arr, l_arr, arr)
    atr_pct = atr / price * 100 if price > 0 else 0.0
    if atr_pct < 0.05:
        return {"final_score": 50.0, "is_trending": False,
                "components": {"atr_pct": round(atr_pct, 4), "filtered": True}}

    # 2. Trend tespiti (EMA20/50 mesafesi)
    s        = pd.Series(arr)
    ema20    = float(s.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50    = float(s.ewm(span=50, adjust=False).mean().iloc[-1])
    trending = (abs(ema20 - ema50) / ema50 * 100 > 1.5) if ema50 > 0 else False

    # 3. İndikatörler
    rsi_s                = _rsi(arr)
    rsi_val              = float(rsi_s.iloc[-1])
    _, _, macd_hist      = _macd(arr)
    bb_up, bb_mid, bb_lo = _bollinger(arr)
    trend_score          = _trend_structure(prices)
    adx_val              = _adx(h_arr, l_arr, arr)   # bilgi amaçlı

    # Hacim skoru
    if volumes and len(volumes) >= 20:
        rv  = float(np.mean(volumes[-5:]))
        pv  = float(np.mean(volumes[-20:-5]))
        chg = (rv - pv) / pv * 100 if pv > 0 else 0.0
        vol_score = float(np.clip(50 + chg / 5, 0, 100))
    else:
        vol_score = 50.0

    # 4. Bileşen skorları
    rsi_score  = rsi_val
    macd_score = float(np.clip(50 + macd_hist * 200, 0, 100))
    bb_score   = _bollinger_score(price, bb_up, bb_lo)
    news_s     = float(np.clip(news_score, 0, 100))

    # 5. Adaptif ağırlıklar (değişmedi)
    if trending:
        w = {"rsi": 0.10, "macd": 0.35, "bb": 0.10, "trend": 0.30, "vol": 0.10, "news": 0.05}
    else:
        w = {"rsi": 0.35, "macd": 0.10, "bb": 0.30, "trend": 0.10, "vol": 0.10, "news": 0.05}

    # 6. Temel skor
    total = (rsi_score  * w["rsi"]  + macd_score * w["macd"] +
             bb_score   * w["bb"]   + trend_score * w["trend"] +
             vol_score  * w["vol"]  + news_s      * w["news"])

    # 7. Bonus: formasyon + uyuşmazlık
    pat_boost = _patterns(arr)
    div_boost = _rsi_divergence(arr, rsi_s.to_numpy())
    total    += pat_boost + div_boost

    return {
        "final_score": round(float(np.clip(total, 0, 100)), 2),
        "is_trending": trending,
        "components": {
            "rsi":       round(rsi_score,  2),
            "macd":      round(macd_score, 2),
            "bollinger": round(bb_score,   2),
            "trend":     round(trend_score,2),
            "volume":    round(vol_score,  2),
            "atr_pct":   round(atr_pct,    4),
            "adx":       round(adx_val,    2),   # trend gücü (skoru etkilemez)
            "patterns":  pat_boost,
            "divergence":div_boost,
            "engine":    INDICATOR_ENGINE,
        }
    }