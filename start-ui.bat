@echo off
rem create by Ortseam
setlocal DisableDelayedExpansion
set "CHROME_PATH="
set "IG_SESSION_DIR="
set "PLAYWRIGHT_NODEJS_PATH="

pushd "%~dp0"
if errorlevel 1 goto folder_tidak_dapat_dibuka
if not exist "runtime\python\python.exe" goto runtime_tidak_ditemukan
if not exist "app\chrome-portable\chrome-win64\chrome.exe" goto chrome_tidak_ditemukan

"runtime\python\python.exe" -B -X utf8 "start_ui.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
goto selesai

:runtime_tidak_ditemukan
echo Runtime Python bawaan tidak ditemukan.
echo Pulihkan seluruh folder "runtime\python" dari ZIP program.
echo Ekstrak paket lengkap; Python sistem tidak dipakai sebagai pengganti.
set "EXIT_CODE=2"
goto selesai

:chrome_tidak_ditemukan
echo Chrome portable bawaan tidak ditemukan.
echo Pulihkan seluruh folder "app\chrome-portable\chrome-win64" dari ZIP program.
echo Jangan hanya menyalin chrome.exe atau menggantinya dengan Chrome sistem.
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
