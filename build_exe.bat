@echo off
setlocal EnableExtensions
cd /d "%~dp0"

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
if not exist ".venv-build\Scripts\python.exe" (
    py -3 -m venv .venv-build
    if errorlevel 1 goto :fail
)

set "PY=.venv-build\Scripts\python.exe"

echo [2/5] Installing/updating PyInstaller...
"%PY%" -m pip install --disable-pip-version-check --upgrade pip pyinstaller
if errorlevel 1 goto :fail

echo [3/5] Cleaning previous build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [4/5] Building GitPulse.exe...
"%PY%" -m PyInstaller --noconfirm --clean --paths "%CD%" GitPulse.spec
if errorlevel 1 goto :fail

if not exist "dist\GitPulse.exe" (
    echo ERROR: PyInstaller completed but dist\GitPulse.exe was not created.
    goto :fail
)

echo [5/5] Verifying packaged imports...
"dist\GitPulse.exe" --self-test
if errorlevel 1 (
    echo ERROR: The EXE was created but failed its package self-test.
    echo This prevents broken builds such as "No module named gitpulse" from being shipped.
    goto :fail
)

echo.
echo SUCCESS
for %%A in ("dist\GitPulse.exe") do echo Built: %%~fA ^(%%~zA bytes^)
echo Icon: assets\gitpulse.ico
echo.
echo IMPORTANT: dist\GitPulse.exe is the real standalone application.
echo It can be copied anywhere and does not need Python or the source folder.
echo.
start "" explorer.exe /select,"%CD%\dist\GitPulse.exe"
pause
exit /b 0

:fail
echo.
echo BUILD FAILED.
echo Read the error above, fix it, then run build_exe.bat again.
pause
exit /b 1
