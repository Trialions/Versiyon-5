# agent.py — Arka plan agent (günlük otomatik rapor)
import datetime
import threading
from agent_reporter import generate_daily_summary_report
from logger import log_info

_lock             = threading.Lock()
_running          = False
_active           = False
_last_report_date = None

TRADE_LOG  = "data/trade_logs.csv"
AGENT_LOG  = "data/agent_summary_report.csv"


def start_agent(active: bool = True):
    global _running, _active
    with _lock:
        _running = True
        _active  = bool(active)
    log_info(f"Agent başlatıldı (aktif={active})")


def stop_agent():
    global _running, _active
    with _lock:
        _running = False
        _active  = False
    log_info("Agent durduruldu")


def is_active() -> bool:
    with _lock:
        return _running and _active


def is_running() -> bool:
    with _lock:
        return _running


def step_agent():
    """
    Simulator arka plan döngüsü tarafından her saniye çağrılır.
    Her gün bir kez günlük özet raporu oluşturur.
    """
    global _last_report_date
    if not is_running():
        return

    today = datetime.date.today()
    with _lock:
        already_done = (_last_report_date == today)

    if not already_done:
        log_info("Agent günlük rapor oluşturuyor...")
        msg = generate_daily_summary_report(TRADE_LOG, AGENT_LOG)
        log_info(f"Agent rapor: {msg}")
        with _lock:
            _last_report_date = today
