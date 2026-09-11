@echo off
setlocal EnableExtensions

REM Resolve project root: scripts\windows\ -> project root
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"

cd /d "%ROOT%"

REM Default: run source version silently
set "TARGET=%SystemRoot%\System32\wscript.exe"
set "ARGS=%ROOT%\scripts\windows\run_source.vbs"

REM Always use the real GitPulse icon for the shortcut
set "ICON=%ROOT%\assets\gitpulse.ico"

REM Prefer standalone EXE when available
if exist "%ROOT%\dist\GitPulse.exe" (
    set "TARGET=%ROOT%\dist\GitPulse.exe"
    set "ARGS="
)

REM Make sure the icon actually exists
if not exist "%ICON%" (
    echo ERROR: GitPulse icon not found:
    echo %ICON%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop=[Environment]::GetFolderPath('Desktop');" ^
  "$shortcutPath=Join-Path $desktop 'GitPulse.lnk';" ^
  "if (Test-Path $shortcutPath) { Remove-Item $shortcutPath -Force };" ^
  "$shell=New-Object -ComObject WScript.Shell;" ^
  "$shortcut=$shell.CreateShortcut($shortcutPath);" ^
  "$shortcut.TargetPath=$env:TARGET;" ^
  "if ($env:ARGS) { $shortcut.Arguments=[char]34 + $env:ARGS + [char]34 };" ^
  "$shortcut.WorkingDirectory=$env:ROOT;" ^
  "$shortcut.IconLocation=$env:ICON + ',0';" ^
  "$shortcut.Description='GitPulse - Life support for GitHub.';" ^
  "$shortcut.Save();"

if errorlevel 1 (
    echo.
    echo Could not create the shortcut.
    pause
    exit /b 1
)

REM Ask Windows Explorer to refresh shortcut icons
if exist "%SystemRoot%\System32\ie4uinit.exe" (
    "%SystemRoot%\System32\ie4uinit.exe" -show >nul 2>&1
)

echo.
echo GitPulse desktop shortcut created with icon:
echo %ICON%
echo.
pause
