@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop=[Environment]::GetFolderPath('Desktop');" ^
  "$shell=New-Object -ComObject WScript.Shell;" ^
  "$shortcut=$shell.CreateShortcut((Join-Path $desktop 'GreenPulse.lnk'));" ^
  "$target=Join-Path '%~dp0' 'GreenPulse.exe';" ^
  "$shortcut.TargetPath=$target;" ^
  "$shortcut.WorkingDirectory='%~dp0';" ^
  "$shortcut.IconLocation=(Join-Path '%~dp0' 'assets\greenpulse.ico') + ',0';" ^
  "$shortcut.Description='GreenPulse — Touch grass. Digitally.';" ^
  "$shortcut.Save()"

if errorlevel 1 (
    echo Could not create the shortcut.
    pause
    exit /b 1
)

echo GreenPulse shortcut created on the Desktop with the GreenPulse icon.
pause
