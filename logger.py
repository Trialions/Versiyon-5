# logger.py — Merkezi hata/olay loglayıcı
import os
import datetime
import threading

LOG_DIR  = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

_lock    = threading.Lock()
_ERR_LOG = os.path.join(LOG_DIR, "errors.log")
_APP_LOG = os.path.join(LOG_DIR, "app.log")


def _write(path: str, msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def log_error(msg: str):
    """Hata logla — errors.log'a yazar, konsola basar."""
    _write(_ERR_LOG, f"ERROR | {msg}")
    print(f"[HATA] {msg}")


def log_info(msg: str):
    """Bilgi logla — app.log'a yazar."""
    _write(_APP_LOG, f"INFO  | {msg}")


def log_event(etype: str, **kw):
    """Engine event'lerini logla."""
    detail = " ".join(f"{k}={v}" for k, v in kw.items())
    _write(_APP_LOG, f"EVENT | {etype} | {detail}")
