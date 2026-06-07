@echo off
chcp 65001 >nul
title Kripto Botu - Backtest v6
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (set PYTHON=py) else (set PYTHON=python)

echo Bagimliliklar kontrol ediliyor...
%PYTHON% -c "import yaml, requests" >nul 2>&1
if errorlevel 1 (
    echo Eksik kutuphane bulundu, yukleniyor...
    pip install PyYAML requests numpy pandas scipy pandas-ta websocket-client --quiet
    echo Kurulum tamamlandi.
)

echo.
echo ============================================
echo  BACKTEST v6
echo ============================================
echo.
echo  [1] Normal backtest
echo  [2] Parametre optimizasyonu
echo  [3] Optimizasyon + config kaydet
echo  [4] Optimizasyon + Out-of-Sample test
echo  [5] Optimizasyon + OOS + config kaydet
echo.
set MOD=1
set /p MOD=Seciminiz (1-5): 

echo.
echo  Test suresi?
echo  [1] Son 7 gun
echo  [2] Son 30 gun  [TAVSIYE]
echo  [3] Son 90 gun
echo  [4] Tarih araligi
echo.
set SURE=2
set /p SURE=Seciminiz (1-4): 

set DAYS=30
set START_DATE=
set END_DATE=

if "%SURE%"=="1" set DAYS=7
if "%SURE%"=="2" set DAYS=30
if "%SURE%"=="3" set DAYS=90
if "%SURE%"=="4" goto ASK_DATE
goto ASK_INTERVAL

:ASK_DATE
set /p START_DATE=Baslangic tarihi (ornek: 2026-01-01): 
set /p END_DATE=Bitis tarihi (ornek: 2026-03-31): 
set DAYS=90

:ASK_INTERVAL
echo.
echo  Mum araligi?
echo  [1] 1 saatlik  [TAVSIYE]
echo  [2] 15 dakika
echo  [3] 4 saatlik
echo.
set INTV=1
set /p INTV=Seciminiz (1-3): 
set INTERVAL=1h
if "%INTV%"=="2" set INTERVAL=15m
if "%INTV%"=="3" set INTERVAL=4h

echo.
set TOP=20
set /p TOP=Kac sembol? (varsayilan 20, max 70): 
if "%TOP%"=="" set TOP=20

echo.
echo ============================================
echo  Calistiriliyor...
echo  Aralik: %INTERVAL% - %TOP% sembol - %DAYS% gun
echo ============================================
echo.

if "%MOD%"=="1" goto RUN_NORMAL
if "%MOD%"=="2" goto RUN_OPT
if "%MOD%"=="3" goto RUN_OPT_SAVE
if "%MOD%"=="4" goto RUN_OOS
if "%MOD%"=="5" goto RUN_OOS_SAVE
goto END

:RUN_NORMAL
if "%START_DATE%"=="" (
    %PYTHON% backtest.py --days %DAYS% --interval %INTERVAL% --top %TOP% --out backtest_results
) else (
    %PYTHON% backtest.py --start %START_DATE% --end %END_DATE% --interval %INTERVAL% --top %TOP% --out backtest_results
)
goto END

:RUN_OPT
if "%START_DATE%"=="" (
    %PYTHON% backtest.py --days %DAYS% --interval %INTERVAL% --top %TOP% --out backtest_results --optimize
) else (
    %PYTHON% backtest.py --start %START_DATE% --end %END_DATE% --interval %INTERVAL% --top %TOP% --out backtest_results --optimize
)
goto END

:RUN_OPT_SAVE
if "%START_DATE%"=="" (
    %PYTHON% backtest.py --days %DAYS% --interval %INTERVAL% --top %TOP% --out backtest_results --optimize --save-config config_online.yaml
) else (
    %PYTHON% backtest.py --start %START_DATE% --end %END_DATE% --interval %INTERVAL% --top %TOP% --out backtest_results --optimize --save-config config_online.yaml
)
goto END

:RUN_OOS
if "%START_DATE%"=="" (
    %PYTHON% backtest.py --days %DAYS% --interval %INTERVAL% --top %TOP% --out backtest_results --optimize --oos
) else (
    %PYTHON% backtest.py --start %START_DATE% --end %END_DATE% --interval %INTERVAL% --top %TOP% --out backtest_results --optimize --oos
)
goto END

:RUN_OOS_SAVE
if "%START_DATE%"=="" (
    %PYTHON% backtest.py --days %DAYS% --interval %INTERVAL% --top %TOP% --out backtest_results --optimize --oos --save-config config_online.yaml
) else (
    %PYTHON% backtest.py --start %START_DATE% --end %END_DATE% --interval %INTERVAL% --top %TOP% --out backtest_results --optimize --oos --save-config config_online.yaml
)
goto END

:END
echo.
echo ============================================
echo  Tamamlandi! Sonuclar: backtest_results
echo ============================================
pause