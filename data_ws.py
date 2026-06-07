# data_ws.py — Binance WebSocket kline akışı (thread-safe, lazy import)
import json
import threading
import time

from logger import log_info, log_error

BINANCE_WS = "wss://stream.binance.com:9443/stream?streams="


class WSFeedKline:
    def __init__(self, symbols: list, on_candle, on_connect=None,
                 interval: str = "1m", shard_size: int = 35):
        self.symbols     = [s.lower() for s in symbols]
        self.on_candle   = on_candle
        self.on_connect  = on_connect
        self.interval    = interval
        self.shard_size  = shard_size

        self.stop_flag       = False
        self._ws_apps        = []
        self._ws_lock        = threading.Lock()
        self._connect_lock   = threading.Lock()
        self._connected_once = False
        self.threads         = []

    # ------------------------------------------------------------------
    def is_open(self) -> bool:
        with self._ws_lock:
            return any(
                getattr(ws.sock, "connected", False)
                for ws in self._ws_apps if ws.sock
            )

    # ------------------------------------------------------------------
    def _run_shard(self, syms: list):
        # Lazy import — websocket-client sadece gerçek bağlantıda gerekli
        try:
            from websocket import WebSocketApp
        except ImportError:
            log_error("websocket-client yüklü değil! Lütfen: pip install websocket-client")
            return

        streams = "/".join(f"{s}@kline_{self.interval}" for s in syms)
        url     = BINANCE_WS + streams

        def on_open(ws):
            log_info(f"WS shard bağlandı: {', '.join(syms[:2])}...")
            with self._connect_lock:
                if not self._connected_once and callable(self.on_connect):
                    try:
                        self.on_connect()
                    except Exception as e:
                        log_error(f"WS on_connect: {e}")
                    self._connected_once = True

        def on_message(ws, msg):
            try:
                payload = json.loads(msg)
                k = payload.get("data", {}).get("k", {})
                if not k or not k.get("x", False):
                    return
                symbol = k.get("s", "")
                if not symbol or not callable(self.on_candle):
                    return
                candle = {
                    "open_time":  int(k["t"]),
                    "open":       float(k["o"]),
                    "high":       float(k["h"]),
                    "low":        float(k["l"]),
                    "close":      float(k["c"]),
                    "volume":     float(k["v"]),
                    "close_time": int(k["T"]),
                    "is_closed":  True,
                }
                self.on_candle(symbol.upper(), candle)
            except Exception as e:
                log_error(f"WS mesaj işleme: {e}")

        def on_error(ws, error):
            log_error(f"WS hata ({syms[0]}...): {error}")

        def on_close(ws, code, msg):
            log_info(f"WS kapandı ({syms[0]}...) kod={code}")

        ws = WebSocketApp(url, on_message=on_message, on_open=on_open,
                          on_error=on_error, on_close=on_close)
        with self._ws_lock:
            self._ws_apps.append(ws)

        while not self.stop_flag:
            try:
                ws.run_forever(ping_interval=20, ping_timeout=10)
                if self.stop_flag:
                    break
                log_info(f"WS koptu ({syms[0]}...), 5s sonra tekrar...")
                time.sleep(5)
            except Exception as e:
                log_error(f"WS döngü ({syms[0]}...): {e}")
                time.sleep(5)

    # ------------------------------------------------------------------
    def start(self):
        with self._ws_lock:
            self._ws_apps.clear()
        self.threads.clear()
        self.stop_flag       = False
        self._connected_once = False

        buckets = [
            self.symbols[i: i + self.shard_size]
            for i in range(0, len(self.symbols), self.shard_size)
        ]
        for bucket in buckets:
            t = threading.Thread(target=self._run_shard,
                                 args=(bucket,), daemon=True)
            t.start()
            self.threads.append(t)
        log_info(f"WS başlatıldı: {len(buckets)} shard, {len(self.symbols)} sembol")

    # ------------------------------------------------------------------
    def stop(self):
        self.stop_flag = True
        with self._ws_lock:
            for ws in self._ws_apps:
                try:
                    ws.close()
                except Exception:
                    pass
        log_info("WS durduruldu")
