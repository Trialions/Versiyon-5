# symbol_manager.py — Otonom Sembol Rotasyon Yöneticisi (v2: küçültme mantığı)
# Kötü performans gösteren sembolü TAMAMEN YASAKLAMAK yerine
# pozisyon boyutunu kademeli KÜÇÜLTÜR. Sembol toparlayınca boyut geri büyür.
# Bu, "en iyi dönemi kaçırma" / kalıcı kilitlenme sorununu çözer.
#
# size_multiplier() → 1.0 (tam), 0.5 (yarı), ... pozisyon boyutu çarpanı döner.
# Hiçbir zaman 0 olmaz (min_multiplier) — sembol asla tamamen susturulmaz,
# böylece toparlanma sinyali yakalanabilir.

from collections import deque, defaultdict


class SymbolManager:
    """
    Sembol bazlı rolling PnL takibi + kademeli pozisyon küçültme.

    config (auto_symbol_filter bloğu):
      enabled        : modül açık/kapalı
      rolling_window : son kaç işlem izlensin (örn 10)
      min_trades     : karar için minimum işlem (örn 5)
      tier1_pnl      : rolling PnL bunun altındaysa boyut %75'e (örn -30)
      tier2_pnl      : rolling PnL bunun altındaysa boyut %50'ye (örn -60)
      tier3_pnl      : rolling PnL bunun altındaysa boyut %25'e (örn -100)
      min_multiplier : asla bunun altına inme (örn 0.25) — tam kilitlenmeyi önler
    """

    def __init__(self, cfg: dict):
        f = cfg.get("auto_symbol_filter", {})
        self.enabled        = bool( f.get("enabled",        True))
        self.rolling_window = int(  f.get("rolling_window",  10))
        self.min_trades     = int(  f.get("min_trades",       5))
        self.tier1_pnl      = float(f.get("tier1_pnl",      -30.0))
        self.tier2_pnl      = float(f.get("tier2_pnl",      -60.0))
        self.tier3_pnl      = float(f.get("tier3_pnl",     -100.0))
        self.min_multiplier = float(f.get("min_multiplier",  0.25))

        self.history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=self.rolling_window)
        )

    def size_multiplier(self, symbol: str) -> float:
        """
        Sembol için pozisyon boyutu çarpanı döner.
        1.0 = tam boyut, 0.75/0.50/0.25 = kademeli küçültme.
        Pozisyon açmadan önce çağrılır, qty bununla çarpılır.
        """
        if not self.enabled:
            return 1.0
        hist = self.history.get(symbol)
        if not hist or len(hist) < self.min_trades:
            return 1.0   # yeterli veri yok → tam boyut

        rolling_pnl = sum(hist)
        if rolling_pnl < self.tier3_pnl:
            mult = 0.25
        elif rolling_pnl < self.tier2_pnl:
            mult = 0.50
        elif rolling_pnl < self.tier1_pnl:
            mult = 0.75
        else:
            mult = 1.0
        return max(mult, self.min_multiplier)

    def record_trade(self, symbol: str, net_pnl: float):
        """Bir işlem (tam) kapandığında çağrılır."""
        if not self.enabled:
            return
        self.history[symbol].append(net_pnl)

    def get_rolling_pnl(self, symbol: str) -> float:
        hist = self.history.get(symbol)
        if not hist or len(hist) == 0:
            return 0.0
        return round(sum(hist), 2)

    def snapshot(self) -> dict:
        reduced = {s: self.size_multiplier(s) for s in self.history
                   if self.size_multiplier(s) < 1.0}
        return {
            "enabled":         self.enabled,
            "reduced_symbols": {s: round(m, 2) for s, m in reduced.items()},
            "tracked_symbols": len(self.history),
        }

    def reset(self):
        self.history.clear()
