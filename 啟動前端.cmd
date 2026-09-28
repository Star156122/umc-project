@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0前端" || goto missing_frontend
set "FRONTEND_NPM="
for /f "delims=" %%I in ('where.exe $PATH:npm.cmd 2^>nul') do if not defined FRONTEND_NPM set "FRONTEND_NPM=%%I"
if not defined FRONTEND_NPM goto missing_npm
echo Starting frontend...
call "%FRONTEND_NPM%" run web
if errorlevel 1 echo [ERROR] Frontend failed to start. See the error above.
pause
exit /b
:missing_frontend
echo [ERROR] Frontend folder was not found.
pause
exit /b 1
:missing_npm
echo [ERROR] npm was not found in PATH. Install Node.js and try again.
pause
exit /b 1