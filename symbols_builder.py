# symbols_builder.py — Binance'ten en yüksek hacimli USDT çiftlerini çeker

import json
import sys
import requests


BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/24hr"


BLACKLIST = {
    # Stable / fiat pegged
    "USDCUSDT",
    "BUSDUSDT",
    "TUSDUSDT",
    "FDUSDUSDT",
    "USDPUSDT",
    "DAIUSDT",
    "USD1USDT",
    "RLUSDUSDT",
    "PYUSDUSDT",
    "USDEUSDT",
    "EURUSDT",
    "EURIUSDT",
    "AEURUSDT",
    "UUSDT",

    # Altın / emtia / wrapped / özel varlıklar
    "XAUTUSDT",
    "PAXGUSDT",
    "XAUUSDT",
    "WBETHUSDT",
}


STABLE_KEYWORDS = (
    "USD",
    "EUR",
    "GBP",
    "TRY",
    "PAXG",
    "XAU",
    "XAUT",
    "WBETH",
)


def is_excluded_symbol(symbol: str) -> bool:
    """
    Stablecoin, fiat, altın/emtia veya özel peg tokenları dışlar.
    """
    symbol = symbol.upper().strip()

    if symbol in BLACKLIST:
        return True

    if not symbol.endswith("USDT"):
        return True

    base = symbol[:-4]  # USDT kısmını çıkar

    for keyword in STABLE_KEYWORDS:
        if keyword in base:
            return True

    return False


def build_top_usdt(n: int = 70, outfile: str = "symbols_top70.json") -> list:
    """
    Binance 24h ticker verisinden en yüksek quoteVolume'a sahip
    USDT paritelerini çeker ve JSON dosyasına yazar.
    """
    try:
        response = requests.get(BINANCE_TICKER, timeout=15)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"[HATA] Binance'e ulaşılamadı: {e}")
        return []

    symbols = []

    for item in data:
        symbol = item.get("symbol", "")

        if is_excluded_symbol(symbol):
            continue

        try:
            quote_volume = float(item.get("quoteVolume", 0))
        except Exception:
            quote_volume = 0.0

        if quote_volume <= 0:
            continue

        symbols.append({
            "symbol": symbol,
            "quoteVolume": quote_volume,
        })

    symbols.sort(key=lambda x: x["quoteVolume"], reverse=True)

    top_symbols = [x["symbol"] for x in symbols[:n]]

    try:
        with open(outfile, "w", encoding="utf-8") as f:
            json.dump(top_symbols, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[HATA] Dosya yazılamadı: {e}")
        return []

    print(f"[OK] {len(top_symbols)} sembol yazıldı → {outfile}")
    print(f"[OK] İlk 10 sembol: {top_symbols[:10]}")

    return top_symbols


if __name__ == "__main__":
    try:
        n = int(sys.argv[1]) if len(sys.argv) > 1 else 70
    except Exception:
        n = 70

    build_top_usdt(n=n)
