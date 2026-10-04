@echo off
rem Starts the Twitch Discord bot on Windows.
rem
rem  - Always runs from the repository folder (the bot reads/writes .env and
rem    *.json files relative to the working directory).
rem  - Creates a virtual environment in .venv on first run and (re)installs
rem    requirements.txt whenever it changes.
rem  - Use --watch to restart the bot automatically if it exits/crashes.
rem
rem Environment overrides:
rem   PYTHON=py -3.11     interpreter used to create the venv (default: py -3, then python)
rem   RESTART_DELAY=10    seconds to wait between restarts in --watch mode
setlocal EnableExtensions

cd /d "%~dp0"

set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%~dp0requirements.txt"
set "REQ_STAMP=%VENV_DIR%\requirements.installed.txt"
if not defined RESTART_DELAY set "RESTART_DELAY=10"

set "WATCH=0"
if /i "%~1"=="--watch" set "WATCH=1"
if /i "%~1"=="-h" goto :usage
if /i "%~1"=="--help" goto :usage

if not exist ".env" (
    echo [start-bot] ERROR: .env not found in %~dp0 ^(needs TOKEN, TWITCH-APP-ID, TWITCH-APP-SECRET^). 1>&2
    exit /b 1
)

rem (gotos instead of a parenthesised block so %PYTHON% expands after it is set)
if exist "%VENV_PY%" goto :deps
if defined PYTHON goto :mkvenv
set "PYTHON=python"
where py >nul 2>&1 && set "PYTHON=py -3"
:mkvenv
echo [start-bot] Creating virtual environment in .venv
%PYTHON% -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo [start-bot] ERROR: could not create venv. Install Python 3 or set PYTHON=... 1>&2
    exit /b 1
)

:deps

if exist "%REQ_FILE%" (
    fc /b "%REQ_FILE%" "%REQ_STAMP%" >nul 2>&1 || (
        echo [start-bot] Installing requirements
        "%VENV_PY%" -m pip install --disable-pip-version-check -q -r "%REQ_FILE%" || exit /b 1
        copy /y "%REQ_FILE%" "%REQ_STAMP%" >nul
    )
)

set "PYTHONUNBUFFERED=1"

:run
echo [start-bot] %date% %time% Starting bot
"%VENV_PY%" "%~dp0Main.py"
set "EXITCODE=%ERRORLEVEL%"
if "%WATCH%"=="0" exit /b %EXITCODE%
echo [start-bot] %date% %time% Bot exited with code %EXITCODE%, restarting in %RESTART_DELAY%s
rem "timeout" fails without a console (scheduled task), so wait with ping instead.
set /a PINGS=RESTART_DELAY+1
ping -n %PINGS% 127.0.0.1 >nul
goto :run

:usage
echo Usage: %~nx0 [--watch]
exit /b 0
