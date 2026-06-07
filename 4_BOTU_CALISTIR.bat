@echo off
chcp 65001 >nul
title Kripto Trade Botu v6
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (set PYTHON=py) else (set PYTHON=python)

echo Bagimliliklar kontrol ediliyor...
%PYTHON% -c "import webview" >nul 2>&1
if errorlevel 1 (
    echo pywebview yukleniyor...
    pip install pywebview --quiet
)

%PYTHON% -c "import yaml" >nul 2>&1
if errorlevel 1 (
    echo Eksik kutuphane yukleniyor...
    pip install PyYAML requests numpy pandas scipy pandas-ta websocket-client --quiet
)

echo Uygulama baslatiliyor...
%PYTHON% app.py
pause