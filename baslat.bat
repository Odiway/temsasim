@echo off
rem Otobus simulasyonu arayuzunu baslatir. Cift tiklayin; tarayici kendiliginden acilir.
rem Kapatmak icin bu pencereyi kapatin ya da Ctrl+C'ye basin.
cd /d "%~dp0"
chcp 65001 >nul

python --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Python bulunamadi. python.org adresinden Python 3.11 ya da ustunu kurun
  echo  ve kurulumda "Add python.exe to PATH" kutusunu isaretleyin.
  echo.
  pause
  exit /b 1
)

python -c "import flask, yaml, openpyxl, numpy, scipy, pandas" >nul 2>&1
if errorlevel 1 (
  echo  Gerekli paketler kuruluyor, bu yalnizca ilk seferde olur...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo  Paketler kurulamadi. Internet baglantisini ya da sirket proxy ayarini kontrol edin.
    pause
    exit /b 1
  )
)

python -m temsasim gui
pause
