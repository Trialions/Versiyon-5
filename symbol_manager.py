# symbol_manager.py — Otonom Sembol Rotasyon Yöneticisi (v3: equity-bazlı tier)
# Kötü performans gösteren sembolü TAMAMEN YASAKLAMAK yerine
# pozisyon boyutunu kademeli KÜÇÜLTÜR. Sembol toparlayınca boyut geri büyür.
# Bu, "en iyi dönemi kaçırma" / kalıcı kilitlenme sorununu çözer.
#
# v2 → v3 DEĞİŞİKLİK:
#   Tier eşikleri artık sabit dolar değil, başlangıç equity'sinin yüzdesi.
#   tier1_pct: -3  → rolling PnL, equity'nin -%3'ünün altına düşerse küçült
#   Bu sayede $1000 equity'de -$30, $500 equity'de -$15 eşiği otomatik ölçeklenir.
#   config'de tier1_pnl/tier2_pnl/tier3_pnl hâlâ varsa fallback olarak kullanılır.
#
# size_multiplier() → 1.0 (tam), 0.75/0.50/0.25 = kademeli küçültme
# min_multiplier    → asla bunun altına inme (tamamen susturmayı önler)

from collections import deque, defaultdict


class SymbolManager:
    """
    Sembol bazlı rolling PnL takibi + kademeli pozisyon küçültme.

    config (auto_symbol_filter bloğu):
      enabled        : modül açık/kapalı
      rolling_window : son kaç işlem izlensin (örn 10)
      min_trades     : karar için minimum işlem (örn 3)
      tier1_pct      : rolling PnL equity'nin bu %'si altındaysa boyut ×0.75 (örn -3.0)
      tier2_pct      : rolling PnL equity'nin bu %'si altındaysa boyut ×0.50 (örn -5.0)
      tier3_pct      : rolling PnL equity'nin bu %'si altındaysa boyut ×0.25 (örn -8.0)
      min_multiplier : asla bunun altına inme (örn 0.25)

      # Geriye dönük uyumluluk — tier_pct yoksa tier_pnl kullanılır:
      tier1_pnl / tier2_pnl / tier3_pnl (sabit dolar, eski davranış)
    """

    def __init__(self, cfg: dict, starting_equity: float = 1000.0):
        f = cfg.get("auto_symbol_filter", {})
        self.enabled        = bool( f.get("enabled",        True))
        self.rolling_window = int(  f.get("rolling_window",  10))
        self.min_trades     = int(  f.get("min_trades",       3))
        self.min_multiplier = float(f.get("min_multiplier",  0.25))
        self.starting_equity = starting_equity

        # Yüzde bazlı tier (yeni, öncelikli)
        self._use_pct = "tier1_pct" in f
        if self._use_pct:
            self.tier1_pct = float(f.get("tier1_pct", -3.0)) / 100
            self.tier2_pct = float(f.get("tier2_pct", -5.0)) / 100
            self.tier3_pct = float(f.get("tier3_pct", -8.0)) / 100
        else:
            # Geriye dönük uyumluluk — sabit dolar
            self.tier1_pnl = float(f.get("tier1_pnl", -30.0))
            self.tier2_pnl = float(f.get("tier2_pnl", -60.0))
            self.tier3_pnl = float(f.get("tier3_pnl", -100.0))

        self.history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=self.rolling_window)
        )
        # Canlı equity takibi (backtest/engine tarafından güncellenir)
        self._current_equity = starting_equity

    def update_equity(self, equity: float):
        """Mevcut equity'yi günceller — tier hesabında kullanılır."""
        self._current_equity = max(equity, 1.0)

    def _thresholds(self):
        """Aktif equity'ye göre tier eşiklerini döner (dolar cinsinden)."""
        if self._use_pct:
            eq = self._current_equity
            return (
                eq * self.tier1_pct,
                eq * self.tier2_pct,
                eq * self.tier3_pct,
            )
        else:
            return (self.tier1_pnl, self.tier2_pnl, self.tier3_pnl)

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
        t1, t2, t3  = self._thresholds()

        if rolling_pnl < t3:
            mult = 0.25
        elif rolling_pnl < t2:
            mult = 0.50
        elif rolling_pnl < t1:
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
        t1, t2, t3 = self._thresholds()
        return {
            "enabled":         self.enabled,
            "equity":          round(self._current_equity, 2),
            "tier_thresholds": {
                "tier1": round(t1, 2),
                "tier2": round(t2, 2),
                "tier3": round(t3, 2),
            },
            "reduced_symbols": {s: round(m, 2) for s, m in reduced.items()},
            "tracked_symbols": len(self.history),
        }

    def reset(self):
        self.history.clear()
