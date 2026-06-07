# utils.py — Genel amaçlı yardımcı fonksiyonlar
import random


def random_walk_price(price: float, step: float = 0.002) -> float:
    """Test/simülasyon için rastgele fiyat hareketi (+/- step oranında)."""
    return max(0.0001, price * (1 + random.uniform(-step, step)))


def fmt_usd(value: float, decimals: int = 2) -> str:
    """Sayıyı USD formatında döndürür. Örn: 1234.5 → '$1,234.50'"""
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


def fmt_pct(value: float, decimals: int = 2) -> str:
    """Sayıyı yüzde formatında döndürür. Örn: 3.5 → '+3.50%'"""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def clamp(value: float, lo: float, hi: float) -> float:
    """Değeri [lo, hi] aralığına kırpar."""
    return max(lo, min(hi, value))
