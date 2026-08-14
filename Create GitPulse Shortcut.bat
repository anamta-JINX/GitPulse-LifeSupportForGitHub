@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "TARGET=%SystemRoot%\System32\wscript.exe"
set "ARGS=%~dp0run_gitpulse.vbs"
set "ICON=%~dp0assets\gitpulse.ico"
if exist "%~dp0dist\GitPulse.exe" (
  set "TARGET=%~dp0dist\GitPulse.exe"
  set "ARGS="
  set "ICON=%~dp0dist\GitPulse.exe"
)

if not exist "%TARGET%" (
  echo GitPulse.exe was not found.
  echo Run build_exe.bat first, then try again.
  pause
  exit /b 1
)

if defined ARGS if not exist "%ARGS%" (
  echo run_gitpulse.vbs was not found. Keep the complete GitPulse folder together.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop=[Environment]::GetFolderPath('Desktop');" ^
  "$shell=New-Object -ComObject WScript.Shell;" ^
  "$shortcut=$shell.CreateShortcut((Join-Path $desktop 'GitPulse.lnk'));" ^
  "$shortcut.TargetPath='%TARGET%';" ^
  "$argPath='%ARGS%';" ^
  "if ($argPath) { $shortcut.Arguments=[char]34 + $argPath + [char]34 };" ^
  "$shortcut.WorkingDirectory='%~dp0';" ^
  "$shortcut.IconLocation='%ICON%,0';" ^
  "$shortcut.Description='GitPulse — Life support for GitHub.';" ^
  "$shortcut.Save()"

if errorlevel 1 (
    echo Could not create the shortcut.
    pause
    exit /b 1
)

echo Desktop shortcut created for:
echo GitPulse v1.5.0
pause
