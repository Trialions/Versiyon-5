@echo off
chcp 65001 >nul
title Kripto Botu - Baglanti Testi
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 ( set PYTHON=py ) else ( set PYTHON=python )

echo Baglanti test ediliyor...
echo.
%PYTHON% run_online_test.py

if errorlevel 1 (
    echo.
    echo [UYARI] Bazi testler basarisiz oldu.
    echo Detay icin logs\smoke_result.json dosyasina bakin.
) else (
    echo.
    echo [OK] Tum testler basarili!
)
echo Simdi 4_BOTU_CALISTIR.bat ile botu baslatabilirsiniz.
pause
