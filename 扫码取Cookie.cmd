@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

echo ============================================
echo   Mihoyo Checkin - QR Login
echo ============================================
echo.
echo [1/2] checking dependencies...
python -m pip install -q requests qrcode pillow

echo [2/2] starting QR login...
echo.
echo   The cookie goes straight into the GitHub COOKIE
echo   secret via gh CLI. It is NOT printed on screen.
echo   If gh is missing, you will be asked before any
echo   fallback to printing it.
echo.
python qr_login.py --set-secret

echo.
echo ============================================
echo  Done. Press any key to close.
echo ============================================
pause >nul
