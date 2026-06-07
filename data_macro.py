# data_macro.py — Fear & Greed Index + BTC Funding Rate (thread-safe, cache'li)
import threading
import time
import requests

from logger import log_info, log_error

CACHE_TTL       = 300   # 5 dakika
REQUEST_TIMEOUT = 8.0

_cache = {
    "sentiment": "NEUTRAL",
    "score":     50.0,
    "ts":        0.0,
    "fng":       "",
    "funding":   0.0,
}
_cache_lock = threading.Lock()


def _fetch_fear_greed() -> tuple:
    """
    alternative.me Fear & Greed Index.
    Dönüş: (değer 0-100, sınıflandırma metni)
    """
    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=REQUEST_TIMEOUT
        )
        r.raise_for_status()
        data  = r.json()["data"][0]
        value = int(data["value"])
        name  = data["value_classification"]
        return value, name
    except Exception as e:
        log_error(f"Fear & Greed fetch hatası: {e}")
        return 50, "Neutral"


def _fetch_btc_funding() -> float:
    """
    Binance BTC/USDT perp funding rate.
    Pozitif → longs ağır basıyor (aşırı iyimser)
    Negatif → shorts ağır basıyor (aşırı kötümser)
    """
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": "BTCUSDT", "limit": 1},
            timeout=REQUEST_TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
        if data:
            return float(data[-1]["fundingRate"])
        return 0.0
    except Exception as e:
        log_error(f"Funding rate fetch hatası: {e}")
        return 0.0


def _combine_sentiment(fng_value: int, funding: float) -> tuple:
    """
    Fear & Greed + Funding Rate'i birleştirip sentiment üretir.

    Mantık:
      fng_value 0-100 (düşük = korku, yüksek = açgözlülük)
      funding   tipik aralık: -0.003 ile +0.003

    Kural seti:
      BULLISH  → fng > 60 VE funding > -0.001  (piyasa iyimser, aşırı short yok)
      BEARISH  → fng < 40 VE funding <  0.001  (piyasa korkuyor, aşırı long yok)
      NEUTRAL  → diğer tüm durumlar

    Skor (0-100):
      Doğrudan fng_value kullan, funding ile ±5 puan düzelt.
    """
    score = float(fng_value)

    # Funding rate düzeltmesi: her 0.001 için ±5 puan
    funding_adj = (funding / 0.001) * 5
    score = max(0.0, min(100.0, score + funding_adj))

    if fng_value > 60 and funding > -0.001:
        sentiment = "BULLISH"
    elif fng_value < 40 and funding < 0.001:
        sentiment = "BEARISH"
    else:
        sentiment = "NEUTRAL"

    return sentiment, round(score, 2)


def _refresh():
    """Cache'i arka planda günceller."""
    try:
        fng_value, fng_name = _fetch_fear_greed()
        funding             = _fetch_btc_funding()
        sentiment, score    = _combine_sentiment(fng_value, funding)

        with _cache_lock:
            _cache.update({
                "sentiment": sentiment,
                "score":     score,
                "ts":        time.time(),
                "fng":       f"{fng_value} ({fng_name})",
                "funding":   funding,
            })
        log_info(
            f"Sentiment → {sentiment} | "
            f"F&G={fng_value}({fng_name}) | "
            f"Funding={funding:.4f} | "
            f"Score={score}"
        )
    except Exception as e:
        log_error(f"Sentiment güncelleme hatası: {e}")
        with _cache_lock:
            _cache["ts"] = time.time() - CACHE_TTL + 60


def get_market_sentiment() -> str:
    now = time.time()
    with _cache_lock:
        fresh  = now < _cache["ts"] + CACHE_TTL
        result = _cache["sentiment"]
    if not fresh:
        threading.Thread(target=_refresh, daemon=True).start()
    return result


def get_sentiment_score() -> float:
    """Sentiment'i 0-100 skoruna çevirir (news_score olarak kullanılır)."""
    now = time.time()
    with _cache_lock:
        fresh = now < _cache["ts"] + CACHE_TTL
        score = _cache["score"]
    if not fresh:
        threading.Thread(target=_refresh, daemon=True).start()
    return score


# Modül yüklenince hemen bir kez güncelle
threading.Thread(target=_refresh, daemon=True).start()