@echo off
rem Start the stall with fake services (no keys, no network): for practice and testing.
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 goto no_uv

uv sync
if errorlevel 1 goto failed
uv run playwright install chromium
if errorlevel 1 goto failed
uv run python -m kiosk --fake
goto done

:no_uv
echo uv is not installed. Install it with this one line, close this window, then run start-fake.bat again:
echo.
echo powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
goto done

:failed
echo.
echo Setup failed. Read the message above.

:done
echo.
pause
