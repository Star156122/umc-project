@echo off
chcp 65001 >nul

cd /d "%~dp0前端\server"

call npm start

pause

