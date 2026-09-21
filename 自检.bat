@echo off
rem ---- agent-eval 一键自检（不需要 API key）----
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/2] 检查依赖...
"%PY%" -c "import yaml" 2>nul || (
  echo   缺 PyYAML，正在安装...
  "%PY%" -m pip install -r requirements.txt
)

echo [2/2] 运行自检...
"%PY%" selftest.py
if errorlevel 1 (
  echo.
  echo [失败] 自检没通过，报告在 reports\
  pause
  exit /b 1
)

echo.
echo [通过] 想看报告： reports\
pause
