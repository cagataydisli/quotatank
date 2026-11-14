@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo.
echo  QuotaTank kurulumu
echo  ==================
echo.

rem ---- Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo  [HATA] Python bulunamadi. https://www.python.org/downloads/ adresinden
  echo         Python 3.10 veya ustunu kur, kurarken "Add python.exe to PATH" isaretle.
  pause
  exit /b 1
)

rem ---- Node.js (Claude Code / Antigravity verisini yakalamak icin)
where node >nul 2>nul
if errorlevel 1 (
  echo  [UYARI] Node.js bulunamadi. Codex calisir, ama Claude Code ve Antigravity
  echo          kotalari icin https://nodejs.org adresinden Node.js kurman gerekir.
  echo.
)

rem ---- Sanal ortam + paketler
if not exist ".venv\Scripts\python.exe" (
  echo  [1/4] Python ortami hazirlaniyor...
  %PY% -m venv .venv || goto :fail
) else (
  echo  [1/4] Python ortami zaten var.
)
echo  [2/4] Gerekli paketler yukleniyor...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail

rem ---- CLI baglantilari (statusLine hook)
echo  [3/4] Claude Code / Antigravity baglantisi kuruluyor...
".venv\Scripts\python.exe" install_hooks.py

rem ---- Masaustu kisayolu
echo  [4/4] Masaustu kisayolu olusturuluyor...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\QuotaTank.lnk\");" ^
  "$s.TargetPath=\"$env:WINDIR\System32\wscript.exe\"; $s.Arguments='\"%~dp0AIQuotaMonitor.vbs\"';" ^
  "$s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0assets\quotatank.ico,0'; $s.Save()"

echo.
echo  Kurulum tamam. QuotaTank aciliyor...
start "" wscript.exe "%~dp0AIQuotaMonitor.vbs"
timeout /t 3 >nul
exit /b 0

:fail
echo.
echo  [HATA] Kurulum tamamlanamadi. Yukaridaki mesaja bak.
pause
exit /b 1
