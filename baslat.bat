@echo off
rem Otobus simulasyonu arayuzunu baslatir. Cift tiklayin; tarayici kendiliginden acilir.
rem Kapatmak icin bu pencereyi kapatin ya da Ctrl+C'ye basin.
rem
rem Ilk calistirmada proje klasorunde ayri bir Python ortami (.venv) kurar ve
rem paketleri oraya yukler. Python'un PATH'te olmasi gerekmez: py baslaticisi,
rem PATH ve bilinen kurulum klasorleri taranir. Yalnizca 3.11-3.14 kullanilir:
rem daha yeni surumler (3.15+) icin numpy/scipy hazir paketleri henuz yok.
rem Yalnizca 64 bit Python kullanilir: 32 bit icin scipy hazir paketi yok ve pip
rem kaynaktan derlemeye kalkip "Visual C++ 14.0 gerekli" hatasi verir.
rem Uygun surum yoksa, istenirse
rem yonetici yetkisi gerektirmeden bu kullanici icin kurulur.
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "PYTMP=%TEMP%\temsasim_python_%RANDOM%.txt"
set "VCHK=import sys, struct; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) and struct.calcsize('P') == 8 else 1)"
set "PY_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"

rem --- 1. Ortam varsa ve calisiyorsa dogrudan kullan -------------------------
if not exist "%VPY%" goto :create_venv
"%VPY%" -c "%VCHK%" >nul 2>&1
if not errorlevel 1 goto :check_pkgs
echo  Mevcut .venv kullanilamiyor (desteklenmeyen ya da 32 bit Python, veya Python kaldirilmis),
echo  yeniden kuruluyor...
rmdir /s /q "%VENV%"

rem --- 2. Ilk kurulum: temel Python'u bul, .venv olustur --------------------
:create_venv
echo.
echo  Ilk kurulum: proje icin ayri bir Python ortami (.venv) olusturuluyor...
call :find_python
if not defined BASEPY call :install_python
if not defined BASEPY goto :no_python

echo  Kullanilan Python: %BASEPY%
"%BASEPY%" -m venv "%VENV%"
if errorlevel 1 goto :venv_failed
if not exist "%VPY%" goto :venv_failed

rem --- 3. Paketler ------------------------------------------------------------
:check_pkgs
"%VPY%" -c "import flask, yaml, openpyxl, numpy, scipy, pandas" >nul 2>&1
if not errorlevel 1 goto :run
echo  Gerekli paketler kuruluyor, bu yalnizca ilk seferde olur...
"%VPY%" -m pip install --disable-pip-version-check --quiet --upgrade pip >nul 2>&1
rem Yalnizca hazir (derlenmis) paketler: derleyici ^(Visual C++^) hic gerekmez.
rem Bir paketin en yeni surumunun hazir paketi yoksa pip bir onceki surumu secer.
"%VPY%" -m pip install --disable-pip-version-check --only-binary=:all: -r requirements.txt
if errorlevel 1 goto :pip_failed

rem --- 4. Calistir ------------------------------------------------------------
:run
"%VPY%" -m temsasim gui
pause
exit /b 0


rem ============================================================================
rem Hata mesajlari
rem ============================================================================
:no_python
echo.
echo  Uygun Python surumu (64 bit, 3.11 - 3.14) bulunamadi ve kurulamadi.
echo  python.org adresinden Python 3.12 ya da 3.13 kurup bu dosyayi tekrar calistirin.
echo  (Diger surumler kurulu kalabilir, birbirini etkilemez.)
echo  (PATH kutusunu isaretlemeniz gerekmez.)
echo.
pause
exit /b 1

:venv_failed
echo.
echo  Python ortami (.venv) olusturulamadi: %BASEPY%
echo  Klasore yazma izniniz oldugundan emin olun, sonra tekrar deneyin.
if exist "%VENV%" rmdir /s /q "%VENV%"
echo.
pause
exit /b 1

:pip_failed
echo.
echo  Paketler kurulamadi. Internet baglantisini ya da sirket proxy ayarini kontrol edin.
echo  "No matching distribution" hatasi gorduyseniz: .venv klasorunu silip bu dosyayi
echo  tekrar calistirin; sorun surerse Python 3.12 ^(64 bit^) kurun.
echo  Proxy gerekiyorsa once su komutu calistirin, sonra baslat.bat'i tekrar acin:
echo    "%VPY%" -m pip config set global.proxy http://KULLANICI:SIFRE@PROXY:PORT
echo.
pause
exit /b 1


rem ============================================================================
rem :find_python  ->  BASEPY degiskenine 3.11-3.14 arasi bir python.exe yolu yazar
rem ============================================================================
:find_python
set "BASEPY="
rem py baslaticisi PATH'e eklenmese de C:\Windows altina kurulur
rem Surumleri tek tek ister: "py -3" en yenisini (orn. 3.15) secerdi
call :try_launcher py
call :try_launcher "%SystemRoot%\py.exe"
call :try_launcher "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
call :try_cmd python
call :try_cmd python3
for %%V in (312 313 311 314) do call :try_exe "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try_exe "%%~fD\python.exe"
for /d %%D in ("%ProgramFiles%\Python3*") do call :try_exe "%%~fD\python.exe"
for /d %%D in ("%ProgramFiles(x86)%\Python3*") do call :try_exe "%%~fD\python.exe"
for /d %%D in ("%SystemDrive%\Python3*") do call :try_exe "%%~fD\python.exe"
call :try_exe "%USERPROFILE%\anaconda3\python.exe"
call :try_exe "%USERPROFILE%\miniconda3\python.exe"
call :try_exe "%LOCALAPPDATA%\anaconda3\python.exe"
call :try_exe "%LOCALAPPDATA%\miniconda3\python.exe"
call :try_exe "%ProgramData%\anaconda3\python.exe"
call :try_exe "%ProgramData%\miniconda3\python.exe"
if exist "%PYTMP%" del "%PYTMP%" >nul 2>&1
goto :eof

rem py baslaticisiyla 64 bit 3.12, 3.13, 3.11, 3.14 surumlerini sirayla dener
:try_launcher
if defined BASEPY goto :eof
if not "%~1"=="py" if not exist "%~1" goto :eof
for %%V in (3.12 3.13 3.11 3.14) do call :try_cmd %1 -%%V-64
goto :eof

rem Bir dosya yolunu dener (yol bosluk icerebilir)
:try_exe
if defined BASEPY goto :eof
if not exist "%~1" goto :eof
call :try_cmd "%~1"
goto :eof

rem Bir komutu dener; Python 3.11-3.14 ise gercek python.exe yolunu BASEPY'ye yazar.
rem Windows Magaza kisayolu (sahte python.exe) burada sessizce elenir.
:try_cmd
if defined BASEPY goto :eof
if exist "%PYTMP%" del "%PYTMP%" >nul 2>&1
%* -c "import sys; import struct; print(sys.executable) if (3, 11) <= sys.version_info[:2] < (3, 15) and struct.calcsize('P') == 8 else None" >"%PYTMP%" 2>nul
if errorlevel 1 goto :eof
set /p BASEPY=<"%PYTMP%"
if defined BASEPY if not exist "%BASEPY%" set "BASEPY="
goto :eof


rem ============================================================================
rem :install_python  ->  python.org kurucusunu bu kullanici icin sessizce kurar
rem ============================================================================
:install_python
echo.
echo  Bu bilgisayarda uygun Python surumu (64 bit, 3.11 - 3.14) bulunamadi.
echo  Daha yeni (orn. 3.15) ya da 32 bit bir Python varsa onunla paketler kurulamiyor.
echo  Python 3.12 bu kullanici icin yaninda kurulabilir; mevcut Python'a
echo  dokunulmaz, yonetici yetkisi gerekmez, yaklasik 25 MB indirilir.
choice /c EH /n /m " Simdi kurulsun mu? [E/H] "
if errorlevel 2 goto :eof

set "PYINST=%TEMP%\python-3.12-temsasim.exe"
if exist "%PYINST%" del "%PYINST%" >nul 2>&1
echo  Indiriliyor...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; [Net.WebRequest]::DefaultWebProxy.Credentials=[Net.CredentialCache]::DefaultNetworkCredentials; Invoke-WebRequest -UseBasicParsing -Uri '%PY_URL%' -OutFile '%PYINST%'" >nul 2>&1
if not exist "%PYINST%" curl.exe -L --fail -s -o "%PYINST%" "%PY_URL%" >nul 2>&1
if not exist "%PYINST%" (
  echo  Indirme basarisiz oldu ^(internet ya da proxy engeli^).
  goto :eof
)

echo  Kuruluyor, bu bir-iki dakika surebilir...
start "" /wait "%PYINST%" /quiet InstallAllUsers=0 InstallLauncherAllUsers=0 PrependPath=0 Include_test=0 Include_doc=0 Shortcuts=0
del "%PYINST%" >nul 2>&1
call :find_python
goto :eof
