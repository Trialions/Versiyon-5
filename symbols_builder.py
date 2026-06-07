# symbols_builder.py — Binance'ten en yüksek hacimli USDT çiftlerini çeker
import json
import sys
import requests

BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/24hr"
_BLACKLIST     = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT"}


def build_top_usdt(n: int = 70, outfile: str = "symbols_top70.json") -> list:
    try:
        r = requests.get(BINANCE_TICKER, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"[HATA] Binance'e ulaşılamadı: {e}")
        return []

    arr  = r.json()
    usdt = [
        x for x in arr
        if x.get("symbol", "").endswith("USDT")
        and x["symbol"] not in _BLACKLIST
        and float(x.get("quoteVolume", 0)) > 0
    ]
    usdt.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
    top = [x["symbol"] for x in usdt[:n]]

    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(top, f, indent=2, ensure_ascii=False)

    print(f"[OK] {len(top)} sembol → {outfile}  |  İlk 5: {top[:5]}")
    return top


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 70
    build_top_usdt(n=n)
