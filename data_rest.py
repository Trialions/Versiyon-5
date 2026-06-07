# data_rest.py — Binance REST API yardımcıları (retry + rate limit koruması)
import time
from typing import List, Dict, Any

import requests
from logger import log_error, log_info

BINANCE_API    = "https://api.binance.com"
_MAX_RETRIES   = 3
_RETRY_DELAY   = 2.0    # saniye (her denemede katlanır)
_REQUEST_DELAY = 0.08   # semboller arası bekleme (rate limit)


def get_klines(symbol: str, interval: str = "1m",
               limit: int = 1000, start_ms: int = None) -> List[List]:
    """
    Binance REST'ten kline (mum) verisi çeker.
    Hata durumunda _MAX_RETRIES kez yeniden dener.
    """
    url    = f"{BINANCE_API}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms:
        params["startTime"] = start_ms

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=10)

            # Rate limit
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 10))
                log_info(f"[REST] Rate limit ({symbol}), {wait:.0f}s bekleniyor...")
                time.sleep(wait)
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.HTTPError as e:
            log_error(f"REST HTTP {symbol}: {e} (deneme {attempt}/{_MAX_RETRIES})")
        except Exception as e:
            log_error(f"REST {symbol}: {e} (deneme {attempt}/{_MAX_RETRIES})")

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY * attempt)

    return []


def parse_klines(raw: List[List]) -> List[Dict[str, Any]]:
    """Ham Binance kline listesini dict listesine çevirir."""
    candles = []
    for k in raw:
        try:
            candles.append({
                "open_time":  int(k[0]),
                "open":       float(k[1]),
                "high":       float(k[2]),
                "low":        float(k[3]),
                "close":      float(k[4]),
                "volume":     float(k[5]),
                "close_time": int(k[6]),
                "trades":     int(k[8]),
            })
        except (IndexError, ValueError):
            continue
    return candles


def preload_klines(symbols: List[str], interval: str = "1m",
                   limit: int = 1000) -> Dict[str, List[Dict]]:
    """
    Tüm sembollerin geçmiş mum verilerini çeker.
    Hata veren semboller atlanır, program durmaz.
    """
    out   = {}
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        try:
            raw       = get_klines(sym, interval=interval, limit=limit)
            out[sym]  = parse_klines(raw)
            if i % 10 == 0:
                log_info(f"Preload: {i}/{total} sembol yüklendi")
        except Exception as e:
            log_error(f"Preload atlandı {sym}: {e}")
        time.sleep(_REQUEST_DELAY)
    return out


def sync_recent_klines(symbol: str, last_close_time_ms: int,
                       interval: str = "1m") -> List[Dict]:
    """Son kapanış zamanından sonraki eksik mumları çeker."""
    raw     = get_klines(symbol, interval=interval, limit=100,
                         start_ms=last_close_time_ms + 1)
    candles = parse_klines(raw)
    return [c for c in candles if c["close_time"] > last_close_time_ms]
