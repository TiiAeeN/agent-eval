@echo off
chcp 65001 >nul
title agent-eval - manual test
cd /d "%~dp0"

set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "ENVFILE=..\..\qq-clone\.env"

echo ============================================================
echo   agent-eval   manual test   (test1)
echo   [1] fill profile    [2] start    [3] chat
echo   [4] type :end to submit         [5] you judge
echo ============================================================
echo.

"%PY%" run_manual_eval.py --env-file "%ENVFILE%"

echo.
echo ============================================================
echo   done. press any key to close.
echo ============================================================
pause >nul
