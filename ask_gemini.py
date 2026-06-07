# ask_gemini.py — İşlem logunu analiz edip Gemini'ye prompt kopyalar
import sys
import os

try:
    import pyperclip
    _CLIP = True
except ImportError:
    _CLIP = False

from agent_reporter import analyze_trades_and_get_prompt

DEFAULT_LOG = "data/trade_logs.csv"


def main():
    log_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG

    if not os.path.exists(log_file):
        print(f"[HATA] Dosya bulunamadı: {log_file}")
        print("Kullanım: python ask_gemini.py [log_dosyasi.csv]")
        return

    print(f"Analiz hazırlanıyor: {log_file}")
    prompt, summary = analyze_trades_and_get_prompt(log_file)

    if not prompt:
        print(f"[HATA] {summary}")
        return

    sep = "=" * 70
    print(f"\n{sep}\nGEMİNİ ANALİZ RAPORU\n{sep}")
    print(f"Özet: {summary}\n{sep}")
    print(prompt)
    print(sep)

    if _CLIP:
        try:
            pyperclip.copy(prompt)
            print("\n[OK] Metin panoya kopyalandı. Gemini'ye yapıştırın.")
        except Exception as e:
            print(f"\n[UYARI] Pano hatası: {e}. Metni manuel kopyalayın.")
    else:
        print("\n[UYARI] pyperclip yüklü değil → pip install pyperclip")


if __name__ == "__main__":
    main()
