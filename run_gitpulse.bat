@echo off
setlocal
cd /d "%~dp0"

where pyw >nul 2>nul
if not errorlevel 1 (
    start "GitPulse" /D "%~dp0" pyw -3 "%~dp0GitPulse.pyw"
    exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "GitPulse" /D "%~dp0" pythonw "%~dp0GitPulse.pyw"
    exit /b 0
)

echo GitPulse needs Python 3.10 or newer when running from source.
echo Install Python from python.org, then run run_gitpulse.bat again.
pause
endlocal
