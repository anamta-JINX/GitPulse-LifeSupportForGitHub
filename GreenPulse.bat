@echo off
setlocal
cd /d "%~dp0"

where pyw >nul 2>nul
if not errorlevel 1 (
    start "GreenPulse" pyw -3 "%~dp0GreenPulse.pyw"
    exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "GreenPulse" pythonw "%~dp0GreenPulse.pyw"
    exit /b 0
)

echo GreenPulse needs Python 3.10 or newer when running from source.
echo Install Python from python.org, then run GreenPulse.bat again.
pause
endlocal
