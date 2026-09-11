@echo off
setlocal EnableExtensions

for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

echo.
echo ========================================
echo   GitPulse Windows EXE Builder
echo ========================================
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python Launcher ^(py.exe^) was not found.
    echo Install Python 3.10 or newer from python.org and enable the Python Launcher.
    goto :fail
)

py -3 -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10 or newer is required'"
if errorlevel 1 goto :fail

echo [1/5] Creating isolated build environment...
if exist ".venv-build" rmdir /s /q ".venv-build"
py -3 -m venv .venv-build
if errorlevel 1 goto :fail

set "PY=.venv-build\Scripts\python.exe"

echo [2/5] Installing build dependencies...
"%PY%" -m pip install --disable-pip-version-check --no-cache-dir -r requirements-dev.txt
if errorlevel 1 goto :fail

echo [3/5] Cleaning previous build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [4/5] Building GitPulse.exe...
"%PY%" -m PyInstaller --noconfirm --clean packaging\GitPulse.spec
if errorlevel 1 goto :fail

if not exist "dist\GitPulse.exe" (
    echo ERROR: PyInstaller completed but dist\GitPulse.exe was not created.
    goto :fail
)

echo [5/5] Verifying packaged imports...
"dist\GitPulse.exe" --self-test
if errorlevel 1 (
    echo ERROR: The EXE was created but failed its package self-test.
    goto :fail
)

echo.
echo SUCCESS
for %%A in ("dist\GitPulse.exe") do echo Built: %%~fA ^(%%~zA bytes^)
echo.
start "" explorer.exe /select,"%CD%\dist\GitPulse.exe"
pause
exit /b 0

:fail
echo.
echo BUILD FAILED.
pause
exit /b 1
