#!/usr/bin/env python3
"""
Online Smoke Test — Bağlantı ve dosya sağlık kontrolü
Kullanım: python run_online_test.py
Çıkış kodu: 0 = başarılı, 1 = hata var
"""
import json
import sys
import time
from pathlib import Path

import requests
from websocket import create_connection, WebSocketTimeoutException

ROOT = Path(__file__).parent
(ROOT / "logs").mkdir(exist_ok=True)

results = {"rest": {}, "ws": {}, "klines": {}, "files": {}, "errors": []}


def check(label, fn):
    print(f"[?] {label}... ", end="", flush=True)
    try:
        fn()
        print("OK")
    except Exception as e:
        results["errors"].append(f"{label}: {e}")
        print(f"HATA — {e}")


# 1. REST ping
def _ping():
    r = requests.get("https://api.binance.com/api/v3/ping", timeout=5)
    r.raise_for_status()
    results["rest"]["ping"] = r.status_code

check("Binance REST ping", _ping)


# 2. Sunucu saat farkı
def _time():
    r  = requests.get("https://api.binance.com/api/v3/time", timeout=5)
    st = r.json()["serverTime"]
    lag = abs(time.time() * 1000 - st)
    results["rest"]["lag_ms"] = round(lag)
    if lag > 1500:
        raise ValueError(f"Saat farkı çok yüksek: {lag:.0f}ms")

check("Sunucu saat farkı (<1500ms)", _time)


# 3. BTCUSDT kline
def _klines():
    r   = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "1m", "limit": 5},
        timeout=8
    )
    raw = r.json()
    results["klines"] = {"count": len(raw), "last_close": float(raw[-1][4])}
    assert len(raw) == 5

check("BTCUSDT kline çekimi (5 mum)", _klines)


# 4. WebSocket bağlantısı
def _ws():
    ws   = create_connection("wss://stream.binance.com:9443/ws/btcusdt@trade", timeout=8)
    msgs = []
    t0   = time.time()
    while time.time() - t0 < 5 and len(msgs) < 3:
        try:
            msgs.append(ws.recv())
        except WebSocketTimeoutException:
            pass
    ws.close()
    results["ws"]["messages"] = len(msgs)
    assert len(msgs) > 0, "Hiç mesaj alınamadı"

check("WebSocket (5s, ≥1 mesaj)", _ws)


# 5. Zorunlu dosyalar
required = [
    "config_online.yaml", "symbols_top70.json",
    "gui.py", "engine.py", "strategy_core.py",
    "data_macro.py", "data_rest.py", "data_ws.py",
    "simulator.py", "agent.py", "agent_reporter.py",
]
for fname in required:
    exists = (ROOT / fname).exists()
    results["files"][fname] = exists
    status = "✓" if exists else "✗ EKSİK"
    print(f"    {fname}: {status}")
    if not exists:
        results["errors"].append(f"Eksik dosya: {fname}")


# Özet
out = ROOT / "logs" / "smoke_result.json"
out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

print("\n" + "=" * 50)
if results["errors"]:
    print(f"HATALAR ({len(results['errors'])}):")
    for e in results["errors"]:
        print(f"  ✗ {e}")
    print(f"\nDetay: {out}")
    sys.exit(1)
else:
    print("Tüm kontroller başarılı ✓")
    print(f"Detay: {out}")
    sys.exit(0)
