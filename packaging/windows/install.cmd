@echo off
rem Double-click to install or upgrade TwitchDiscordBot. Extra options are passed to install.ps1,
rem e.g.  install.cmd -Reconfigure   or   install.cmd -Uninstall -Purge
setlocal
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    ) else (
        powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%*'"
    )
    exit /b
)
rem Run from %TEMP% so uninstalling can delete the install folder even when started from inside it.
pushd "%TEMP%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "CODE=%ERRORLEVEL%"
popd
echo.
pause
exit /b %CODE%
