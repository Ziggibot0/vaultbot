@echo off
REM Auto-generated restart script. Self-deleting.
cd /d "%~dp0.."
REM Wait for port 8000 to be free (old process dying)
:wait_port
timeout /t 1 /nobreak >nul
netstat -an | findstr ":8000 " | findstr "LISTENING" >nul
if %errorlevel%==0 goto wait_port
REM Small extra delay for cleanup
timeout /t 1 /nobreak >nul
REM Port is free, start new backend
call "%~dp0..\start_backend.bat"
REM Delete this script
del "%~f0"
