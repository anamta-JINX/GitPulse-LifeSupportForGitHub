@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

where py >nul 2>nul
if not errorlevel 1 (
    py -3 GitPulse.pyw
    exit /b %errorlevel%
)

where python >nul 2>nul
if not errorlevel 1 (
    python GitPulse.pyw
    exit /b %errorlevel%
)

echo GitPulse needs Python 3.10 or newer when running from source.
echo Install Python from python.org and try again.
pause
exit /b 1
