@echo off
setlocal EnableExtensions
title Multi-Market Trading Bot
cd /d "%~dp0"

:: ---------- 1. find Python ----------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto no_python

:: ---------- 2. one-time setup (virtual env + packages) ----------
if exist ".venv\Scripts\python.exe" goto check_deps
echo [setup] Creating virtual environment in .venv ...
%PY% -m venv .venv
if errorlevel 1 goto fail
:check_deps
set "VPY=.venv\Scripts\python.exe"
if exist ".venv\installed.flag" goto check_env
echo [setup] Installing required packages (first run only, may take a few minutes) ...
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo [setup] Installing MetaTrader5 connector (forex and metals, Windows only) ...
"%VPY%" -m pip install MetaTrader5
if errorlevel 1 echo [warning] MetaTrader5 did not install. Forex/metals will not work until it does.
echo installed > ".venv\installed.flag"

:check_env
if exist ".env" goto menu
copy /y ".env.example" ".env" >nul
echo [setup] Created .env from .env.example. Add your API keys there later for live trading.

:menu
cls
echo ==============================================================
echo   MULTI-MARKET TRADING BOT  (crypto, forex, metals)
echo ==============================================================
echo   1. PAPER TRADE  - simulated money on real prices  [RECOMMENDED]
echo   2. LIVE TRADE   - places REAL orders with REAL money
echo   3. BACKTEST     - test the strategy on past data
echo   4. STATUS       - show cash, open positions, halts
echo   5. CLEAR HALTS  - re-enable trading after a stop-out
echo   6. EDIT CONFIG  - open config.yaml and .env in Notepad
echo   7. EXIT
echo ==============================================================
set "CHOICE="
set /p "CHOICE=Choose 1-7 and press Enter: "
if "%CHOICE%"=="1" goto paper
if "%CHOICE%"=="2" goto live
if "%CHOICE%"=="3" goto backtest
if "%CHOICE%"=="4" goto status
if "%CHOICE%"=="5" goto clear
if "%CHOICE%"=="6" goto edit
if "%CHOICE%"=="7" goto end
goto menu

:paper
echo.
echo Starting PAPER trading. Nothing real is traded. Press Ctrl+C to stop.
echo To stop cleanly you can also create a file named STOP_BOT in this folder.
echo.
"%VPY%" -m bot --config config.yaml run --mode paper
goto after

:live
echo.
echo ***************************************************************
echo  WARNING: LIVE MODE USES REAL MONEY.
echo  - You can lose some or all of the money in your account.
echo  - Run PAPER mode for several weeks and review the results first.
echo  - Use an exchange API key with trading ON and WITHDRAWALS OFF.
echo  - Never run this on money you cannot afford to lose.
echo ***************************************************************
set "CONFIRM="
set /p "CONFIRM=Type I UNDERSTAND (in capitals) to continue, or press Enter to go back: "
if not "%CONFIRM%"=="I UNDERSTAND" goto menu
set "LIVE_CONFIRM=YES"
echo Starting LIVE trading. Press Ctrl+C to stop.
"%VPY%" -m bot --config config.yaml run --mode live
set "LIVE_CONFIRM="
goto after

:backtest
set "MKT="
set "SYM="
echo Markets: crypto, forex, metals
set /p "MKT=Market (e.g. crypto): "
set /p "SYM=Symbol (e.g. BTC/USDT or EURUSD or XAUUSD): "
"%VPY%" -m bot --config config.yaml backtest --market %MKT% --symbol "%SYM%" --bars 2000
goto after

:status
"%VPY%" -m bot --config config.yaml status
goto after

:clear
"%VPY%" -m bot --config config.yaml clear-halt
goto after

:edit
start notepad config.yaml
start notepad .env
goto menu

:no_python
echo Python 3.9 or newer was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1

:fail
echo.
echo Setup failed. Read the messages above, fix the problem, and run this file again.
pause
exit /b 1

:after
echo.
echo The bot has stopped.
pause
goto menu

:end
endlocal
exit /b 0
