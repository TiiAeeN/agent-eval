@echo off
rem ---- agent-eval 一键自检（不需要 API key）----
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/3] 检查依赖...
"%PY%" -c "import yaml" 2>nul || (
  echo   缺 PyYAML，正在安装...
  "%PY%" -m pip install -r requirements.txt
)

echo.
echo [2/3] 工具型评测自检...
"%PY%" selftest.py
if errorlevel 1 (
  echo.
  echo [失败] 工具型自检没通过，报告在 reports\
  pause
  exit /b 1
)

echo.
echo [3/3] 对话型判分器自检...
"%PY%" selftest_dialogue.py
if errorlevel 1 (
  echo.
  echo [失败] 对话型判分器自检没过 —— 判分器自己有问题，这时候跑真模型得到的分不能信
  pause
  exit /b 1
)

echo.
echo [通过] 两套自检全绿。想看报告： reports\
pause
