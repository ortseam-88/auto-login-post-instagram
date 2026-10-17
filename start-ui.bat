@echo off
rem create by Ortseam
setlocal DisableDelayedExpansion
set "IG_SESSION_DIR="
set "PLAYWRIGHT_NODEJS_PATH="

pushd "%~dp0"
if errorlevel 1 goto folder_tidak_dapat_dibuka

rem 1. Gunakan Python bawaan paket jika tersedia
if exist "runtime\python\python.exe" (
    "runtime\python\python.exe" -B -X utf8 "start_ui.py" %*
    set "EXIT_CODE=%ERRORLEVEL%"
    goto selesai
)

rem 2. Jika clone dari Git, coba gunakan Python sistem
where python >nul 2>&1
if not errorlevel 1 (
    python -B -X utf8 "start_ui.py" %*
    set "EXIT_CODE=%ERRORLEVEL%"
    goto selesai
)

rem 3. Jika tidak ada Python sama sekali
echo =====================================================================
echo Runtime Python tidak ditemukan di PC Anda.
echo.
echo Pilihan solusi:
echo 1. Unduh paket portable lengkap siap pakai (termasuk Python & Chrome):
echo    https://github.com/ortseam-88/auto-login-post-instagram/releases
echo.
echo 2. Atau pasang Python 3.10+ di Windows dan jalankan:
echo    pip install -r app/requirements.txt
echo    python start_ui.py
echo =====================================================================
set "EXIT_CODE=2"
goto selesai
:selesai
popd
goto keluar

:folder_tidak_dapat_dibuka
echo Folder program tidak dapat dibuka.
echo Ekstrak ZIP ke folder lokal yang dapat ditulis, lalu jalankan kembali.
set "EXIT_CODE=2"

:keluar
pause
endlocal & exit /b %EXIT_CODE%
