@echo off
chcp 65001 >nul
title Kripto Botu Kurulum

cd /d "%~dp0"

echo ============================================
echo  Kripto Botu - Kurulum
echo ============================================
echo.

REM Python kontrol
where python >nul 2>&1
if errorlevel 1 (
    where py >nul 2>&1
    if errorlevel 1 (
        echo [HATA] Python bulunamadi!
        echo Lutfen su adimları takip edin:
        echo  1. https://www.python.org/downloads/ adresine gid
        echo  2. Indirip kurun
        echo  3. Kurulumda "Add Python to PATH" secenegini isaretleyin
        echo  4. Bilgisayari yeniden baslatın
        echo  5. Bu dosyayi tekrar calistirin
        pause
        exit /b 1
    )
    set PYTHON=py
) else (
    set PYTHON=python
)

echo [OK] Python bulundu:
%PYTHON% --version
echo.
echo Kutuphaneler yukleniyor, bekleyin...
echo.

%PYTHON% -m pip install --upgrade pip
%PYTHON% -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [HATA] Kurulum basarisiz!
    echo Internet baglantinizi kontrol edin.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Kurulum tamamlandi!
echo  Simdi 2_SEMBOLLER.bat calistirin.
echo ============================================
pause
