@echo off
chcp 65001 >nul
title Kripto Botu - Sembol Listesi
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 ( set PYTHON=py ) else ( set PYTHON=python )

echo Binance sembol listesi indiriliyor...
echo.
%PYTHON% symbols_builder.py

if errorlevel 1 (
    echo [HATA] Semboller indirilemedi.
    pause
    exit /b 1
)
echo.
echo [OK] symbols_top70.json olusturuldu.
echo Simdi 3_BAGLANTI_TEST.bat calistirin.
pause
