@echo off
chcp 65001 >nul
echo ========================================
echo   jztz_v17 Windows 服务安装脚本
echo ========================================
echo.
echo 请以管理员身份运行此脚本！
echo.
pause

:: ====== 配置 ======
set APP_DIR=D:\UI\jztz_v17
set TASK_NAME=jztz_v17_stock_picker

:: ====== 创建计划任务（用户登录时启动，后台运行） ======
schtasks /create /tn "%TASK_NAME%" /tr "python %APP_DIR%\web_app.py" /sc ONLOGON /delay 0000:10 /rl HIGHEST /f

:: ====== 立即启动 ======
schtasks /run /tn "%TASK_NAME%"

echo.
echo ✅ 计划任务已创建！
echo    任务名: %TASK_NAME%
echo    启动方式: 用户登录后自动启动
echo    手动启动: schtasks /run /tn "%TASK_NAME%"
echo    手动停止: schtasks /end /tn "%TASK_NAME%"
echo    查看状态: schtasks /query /tn "%TASK_NAME%"
echo.
pause
