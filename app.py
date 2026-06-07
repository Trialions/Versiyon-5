# app.py — Kripto Trade Botu v6 — PyWebView Köprüsü
import json
import time
import threading
import subprocess
import sys
import os
import csv as csv_mod
import shutil
import yaml
import webview

from simulator import (get_status, get_open_status, get_pnl,
                       start_realtime, stop_realtime,
                       add_to_blacklist, remove_from_blacklist, get_blacklist,
                       get_hourly_stats, get_coin_stats)
from optimizer import run_optimization

try:
    from agent import start_agent, stop_agent, is_active as agent_is_active
except ImportError:
    start_agent = stop_agent = lambda *a: None
    agent_is_active = lambda: False

_APP_DIR       = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH    = os.path.join(_APP_DIR, "config_online.yaml")
BT_BASE_DIR    = os.path.join(_APP_DIR, "backtest_results")
WF_BASE_DIR    = os.path.join(_APP_DIR, "walkforward_results")
_log_buffer    = []
_log_lock      = threading.Lock()
_bt_proc       = None
_bt_lock       = threading.Lock()
_bt_active_dir = None   # o an çalışan testin klasörü
_wf_proc       = None
_wf_lock       = threading.Lock()
_wf_active_dir = ""
_wf_proc       = None
_wf_lock       = threading.Lock()
_wf_active_dir = ""


# ── Log Yönetimi ──────────────────────────────────────────────
def _push_log(msg: str):
    ts   = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _log_lock:
        _log_buffer.append(line)
        if len(_log_buffer) > 500:
            _log_buffer.pop(0)


# ── Backtest Klasör Yardımcıları ──────────────────────────────
def _make_bt_dir(params: dict) -> str:
    """Tarih damgalı alt klasör oluşturur, config snapshot kaydeder."""
    stamp    = time.strftime("%Y-%m-%d_%H-%M")
    interval = params.get("interval", "1h")
    days     = params.get("days", 30)
    mode     = params.get("mode", "normal")
    folder   = f"{stamp}_{interval}_{days}d_{mode}"
    path     = os.path.join(BT_BASE_DIR, folder)
    os.makedirs(path, exist_ok=True)

    # Config snapshot kaydet
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            risk  = cfg.get("risk", {})
            thr   = cfg.get("thresholds", {})
            m     = cfg.get("mode", {})
            btc_f = cfg.get("btc_filter", {})
            adx_f = cfg.get("adx_filter", {})
            ptp   = cfg.get("partial_tp", {})
            mtf   = cfg.get("mtf", {})
        snap  = {
            "timestamp":            time.strftime("%Y-%m-%d %H:%M:%S"),
            "interval":             params.get("interval", "1h"),
            "days":                 params.get("days",     30),
            "top":                  params.get("top",      20),
            "mode":                 params.get("mode",     "normal"),
            "score_long_open":      thr.get("score_long_open",      85),
            "score_short_open":     thr.get("score_short_open",     5),
            "hard_stop_pct":        risk.get("hard_stop_pct",       2.5),
            "take_profit_min_pct":  risk.get("take_profit_min_pct", 3.0),
            "trailing_step_pct":    risk.get("trailing_step_pct",   1.0),
            "atr_multiplier":       risk.get("atr_multiplier",      2.5),
            "min_profit_close_pct": risk.get("min_profit_close_pct",2.0),
            "use_atr_stop":         risk.get("use_atr_stop",        True),
            "max_stop_pct":         risk.get("max_stop_pct",        4.5),
            "btc_filter_enabled":   btc_f.get("enabled",           True),
            "btc_filter_lookback":  btc_f.get("lookback_candles",     4),
            "btc_filter_drop_pct":  btc_f.get("drop_pct",           1.5),
            "adx_filter_enabled":   adx_f.get("enabled",          False),
            "adx_filter_threshold": adx_f.get("threshold",         25.0),
            "partial_tp_enabled":   ptp.get("enabled",             True),
            "partial_tp_r_mult":    ptp.get("tp1_r_mult",          0.75),
            "partial_tp_close_pct": ptp.get("close_pct",           0.50),
            "mtf_enabled":            mtf.get("enabled",           True),
            "htf_long_min":           mtf.get("htf_long_min",      55.0),
            "htf_short_max":          mtf.get("htf_short_max",     45.0),
            "htf_interval":           mtf.get("htf_interval",      "1h"),
            "risk_per_trade_pct":     risk.get("risk_per_trade_pct", 1.0),
            "min_hold_minutes":       risk.get("min_hold_minutes",   30),
        }
        snap_path = os.path.join(path, "config_snapshot.json")
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _push_log(f"[UYARI] Config snapshot kaydedilemedi: {e}")

    return path


def _load_bt_dir(path: str) -> dict:
    """Verilen klasörden tüm backtest verilerini okur."""
    results = {"summary": {}, "trades": [], "equity": [], "config": {}, "folder": path}

    try:
        s_path = os.path.join(path, "backtest_summary.csv")
        if os.path.exists(s_path):
            with open(s_path, newline="", encoding="utf-8-sig") as f:
                for row in csv_mod.reader(f, delimiter=";"):
                    if len(row) >= 2:
                        results["summary"][row[0]] = row[1]

        t_path = os.path.join(path, "backtest_trades.csv")
        if os.path.exists(t_path):
            with open(t_path, newline="", encoding="utf-8-sig") as f:
                reader = csv_mod.DictReader(f, delimiter=";")
                results["trades"] = list(reader)[:200]

        e_path = os.path.join(path, "equity_curve.csv")
        if os.path.exists(e_path):
            with open(e_path, newline="", encoding="utf-8-sig") as f:
                reader = csv_mod.DictReader(f, delimiter=";")
                rows   = list(reader)
                step   = max(1, len(rows) // 200)
                results["equity"] = [
                    {"t": r["Timestamp"], "v": float(r["Equity"])}
                    for r in rows[::step]
                ]

        c_path = os.path.join(path, "config_snapshot.json")
        if os.path.exists(c_path):
            with open(c_path, encoding="utf-8") as f:
                results["config"] = json.load(f)

    except Exception as e:
        results["error"] = str(e)

    return results


def _bt_list_meta() -> list:
    """Tüm geçmiş testlerin özet listesini döner (en yeni önce)."""
    if not os.path.exists(BT_BASE_DIR):
        return []
    folders = sorted(
        [d for d in os.listdir(BT_BASE_DIR)
         if os.path.isdir(os.path.join(BT_BASE_DIR, d))],
        reverse=True
    )
    result = []
    for folder in folders:
        path   = os.path.join(BT_BASE_DIR, folder)
        meta   = {"folder": folder, "path": path}

        # config snapshot varsa oku
        c_path = os.path.join(path, "config_snapshot.json")
        if os.path.exists(c_path):
            try:
                with open(c_path, encoding="utf-8") as f:
                    meta["config"] = json.load(f)
            except Exception:
                meta["config"] = {}
        else:
            meta["config"] = {}

        # summary CSV'den sadece kritik metrikleri al
        s_path = os.path.join(path, "backtest_summary.csv")
        meta["summary"] = {}
        if os.path.exists(s_path):
            try:
                with open(s_path, newline="", encoding="utf-8-sig") as f:
                    for row in csv_mod.reader(f, delimiter=";"):
                        if len(row) >= 2:
                            meta["summary"][row[0]] = row[1]
            except Exception:
                pass

        result.append(meta)
    return result


# ── Python↔JS API Sınıfı ─────────────────────────────────────
class API:

    # ── Bot Kontrolü ──────────────────────────────────────────
    def start_bot(self):
        def _run():
            start_realtime(log_callback=_push_log)
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True}

    def stop_bot(self):
        stop_realtime(log_callback=_push_log)
        return {"ok": True}

    def get_state(self):
        st  = get_status()
        pnl = get_pnl()
        return {
            "ws":       st.get("ws",       "-"),
            "universe": st.get("universe", 0),
            "shards":   st.get("shards",   0),
            "preload":  st.get("preload",  False),
            "top5":     st.get("top5",     ""),
            "pnl_usd":  pnl.get("usd",       0.0),
            "pnl_pct":  pnl.get("pct",       0.0),
            "daily":    pnl.get("daily_usd", 0.0),
            "equity":   pnl.get("equity",    0.0),
        }

    def get_positions(self):
        return get_open_status()

    def get_logs(self, since_index: int = 0):
        with _log_lock:
            return {
                "lines": _log_buffer[since_index:],
                "total": len(_log_buffer),
            }

    # ── Sembol Güncelle ───────────────────────────────────────
    def update_symbols(self):
        def _run():
            try:
                from symbols_builder import build_top_usdt
                build_top_usdt()
                _push_log("[OK] symbols_top70.json güncellendi.")
            except Exception as e:
                _push_log(f"[HATA] Sembol güncelleme: {e}")
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True}

    # ── Agent ─────────────────────────────────────────────────
    def toggle_agent(self, active: bool):
        if active:
            start_agent()
            _push_log("[Agent] Günlük raporlama aktif.")
        else:
            stop_agent()
            _push_log("[Agent] Raporlama durduruldu.")
        return {"ok": True, "active": active}

    # ── Optimizasyon ──────────────────────────────────────────
    def run_optimize(self):
        def _run():
            try:
                result = run_optimization()
                for line in result.split("\n"):
                    _push_log(line)
            except Exception as e:
                _push_log(f"[HATA] Optimizasyon: {e}")
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True}

    # ── Kara Liste ────────────────────────────────────────────
    def get_blacklist(self):
        return get_blacklist()

    def blacklist_add(self, symbol: str, hours: float):
        sym = symbol.strip().upper()
        if not sym.endswith("USDT"):
            sym += "USDT"
        add_to_blacklist(sym, hours)
        _push_log(f"[Kara Liste] {sym} eklendi ({hours:.0f} saat)")
        return {"ok": True}

    def blacklist_remove(self, symbol: str):
        sym = symbol.strip().upper()
        if not sym.endswith("USDT"):
            sym += "USDT"
        remove_from_blacklist(sym)
        _push_log(f"[Kara Liste] {sym} çıkarıldı")
        return {"ok": True}

    # ── İstatistikler ─────────────────────────────────────────
    def get_hourly_stats(self):
        return get_hourly_stats()

    def get_coin_stats(self):
        return get_coin_stats()

    # ── Config ────────────────────────────────────────────────
    def get_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return {"ok": True, "data": yaml.safe_load(f)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_config(self, data: dict):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)
            _push_log("[Config] Ayarlar kaydedildi.")
            return {"ok": True}
        except Exception as e:
            _push_log(f"[HATA] Config kayıt: {e}")
            return {"ok": False, "error": str(e)}

    # ── Backtest ──────────────────────────────────────────────
    def start_backtest(self, params: dict):
        global _bt_proc, _bt_active_dir
        with _bt_lock:
            if _bt_proc and _bt_proc.poll() is None:
                return {"ok": False, "error": "Backtest zaten çalışıyor."}

        # Tarih damgalı klasör aç ve config snapshot kaydet
        out_dir        = _make_bt_dir(params)
        _bt_active_dir = out_dir

        days     = str(params.get("days",     30))
        interval = str(params.get("interval", "1h"))
        top      = str(params.get("top",      20))
        mode     = str(params.get("mode",     "normal"))
        start    = str(params.get("start",    ""))
        end      = str(params.get("end",      ""))

        cmd = [sys.executable, "backtest.py",
               "--days", days, "--interval", interval,
               "--top", top, "--out", out_dir]

        if start and end:
            cmd += ["--start", start, "--end", end]
        if mode in ("optimize", "opt_save", "oos", "oos_save"):
            cmd.append("--optimize")
        if mode in ("oos", "oos_save"):
            cmd.append("--oos")
        if mode in ("opt_save", "oos_save"):
            cmd += ["--save-config", CONFIG_PATH]

        def _run():
            global _bt_proc
            _push_log(f"[Backtest] Başlatıldı → {out_dir}")
            try:
                _bt_env = dict(os.environ)
                _bt_env["PYTHONIOENCODING"] = "utf-8"
                _bt_env["PYTHONUTF8"] = "1"
                _bt_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    bufsize=1,
                    env=_bt_env
                )
                for line in _bt_proc.stdout:
                    line = line.rstrip()
                    if line:
                        _push_log(f"[BT] {line}")
                _bt_proc.wait()
                code = _bt_proc.returncode
                if code == 0:
                    _push_log("[Backtest] Tamamlandı.")
                else:
                    _push_log(f"[Backtest] Hata kodu: {code}")
            except Exception as e:
                _push_log(f"[Backtest] Çalıştırma hatası: {e}")

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "dir": out_dir}

    def stop_backtest(self):
        global _bt_proc
        with _bt_lock:
            if _bt_proc and _bt_proc.poll() is None:
                _bt_proc.terminate()
                _push_log("[Backtest] Durduruldu.")
                return {"ok": True}
        return {"ok": False, "error": "Çalışan backtest yok."}

    def get_backtest_status(self):
        global _bt_proc, _bt_active_dir
        with _bt_lock:
            running = _bt_proc is not None and _bt_proc.poll() is None
        return {"running": running, "dir": _bt_active_dir}

    def get_backtest_results(self, folder: str = ""):
        """Verilen klasörün (veya aktif testin) sonuçlarını döner."""
        global _bt_active_dir
        if folder:
            path = folder if os.path.isabs(folder) else os.path.join(BT_BASE_DIR, folder)
        else:
            path = _bt_active_dir or ""
        if not path or not os.path.exists(path):
            path = BT_BASE_DIR
        return _load_bt_dir(path)

    # ── Walk-Forward ────────────────────────────────────────────
    def start_walkforward(self, params: dict):
        global _wf_proc, _wf_active_dir
        with _wf_lock:
            if _wf_proc and _wf_proc.poll() is None:
                return {"ok": False, "error": "Walk-forward zaten çalışıyor."}
        import time as _time
        stamp    = _time.strftime("%Y-%m-%d_%H-%M")
        start    = str(params.get("start", ""))
        end      = str(params.get("end",   ""))
        interval = str(params.get("interval", "1h"))
        top      = str(params.get("top", 20))
        folder   = f"{stamp}_wf_{interval}"
        out_dir  = os.path.join(WF_BASE_DIR, folder)
        os.makedirs(out_dir, exist_ok=True)
        _wf_active_dir = out_dir
        cmd = [sys.executable, "walk_forward.py",
               "--start", start, "--end", end,
               "--interval", interval, "--top", top,
               "--out", out_dir]
        def _run():
            global _wf_proc
            _push_log(f"[WF] Başlatıldı → {out_dir}")
            try:
                _wf_env = dict(os.environ)
                _wf_env["PYTHONIOENCODING"] = "utf-8"
                _wf_env["PYTHONUTF8"] = "1"
                _wf_proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    bufsize=1, env=_wf_env
                )
                for line in _wf_proc.stdout:
                    line = line.rstrip()
                    if line: _push_log(f"[WF] {line}")
                _wf_proc.wait()
                code = _wf_proc.returncode
                _push_log("[WF] Tamamlandı." if code == 0 else f"[WF] Hata kodu: {code}")
            except Exception as e:
                _push_log(f"[WF] Çalıştırma hatası: {e}")
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "dir": out_dir}

    def stop_walkforward(self):
        global _wf_proc
        with _wf_lock:
            if _wf_proc and _wf_proc.poll() is None:
                _wf_proc.terminate()
                return {"ok": True}
            return {"ok": False}

    def get_wf_status(self):
        return {"running": bool(_wf_proc and _wf_proc.poll() is None),
                "dir": _wf_active_dir}

    def get_wf_history(self):
        if not os.path.exists(WF_BASE_DIR):
            return []
        folders = sorted([d for d in os.listdir(WF_BASE_DIR)
                          if os.path.isdir(os.path.join(WF_BASE_DIR, d))],
                         reverse=True)
        result = []
        for folder in folders[:20]:
            path = os.path.join(WF_BASE_DIR, folder)
            sp = os.path.join(path, "wf_summary.json")
            if os.path.exists(sp):
                try:
                    with open(sp, encoding="utf-8") as f:
                        s = json.load(f)
                    result.append({"folder": folder, "summary": s})
                except Exception:
                    pass
        return result

    def get_wf_results(self, folder: str):
        path = folder if os.path.isabs(folder) else os.path.join(WF_BASE_DIR, folder)
        out = {"summary": {}, "monthly": []}
        try:
            with open(os.path.join(path,"wf_summary.json"), encoding="utf-8") as f:
                out["summary"] = json.load(f)
        except Exception:
            pass
        try:
            with open(os.path.join(path,"wf_monthly.csv"),
                      newline="", encoding="utf-8-sig") as f:
                out["monthly"] = list(csv.DictReader(f, delimiter=";"))
        except Exception:
            pass
        return out

    def get_backtest_history(self):
        """Tüm geçmiş testlerin listesini döner."""
        return _bt_list_meta()

    def delete_backtest(self, folder: str):
        """Bir geçmiş testi siler."""
        path = os.path.join(BT_BASE_DIR, folder)
        try:
            if os.path.exists(path):
                shutil.rmtree(path)
                _push_log(f"[Backtest] Silindi: {folder}")
                return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "Klasör bulunamadı."}


# ── PyWebView Başlatma ────────────────────────────────────────
if __name__ == "__main__":
    api    = API()
    window = webview.create_window(
        title    = "Kripto Trade Botu — v6",
        url      = "gui.html",
        js_api   = api,
        width    = 1400,
        height   = 880,
        min_size = (1000, 700),
        resizable= True,
    )
    webview.start(debug=False)