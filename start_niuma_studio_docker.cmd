@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo 正在启动牛马片场 Docker 和 Windows opencli 辅助服务...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_docker_opencli.ps1"
if errorlevel 1 (
  echo.
  echo 启动失败：请确认 Docker Desktop 已打开，并且 Windows 已安装 opencli。
  echo 如果这里显示 opencli 找不到，请先安装 opencli 后再双击本文件。
  pause
  exit /b 1
)

echo.
echo 已完成启动。如果浏览器没有自动打开，请访问 http://127.0.0.1:8001/publish
pause
