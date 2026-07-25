@echo off
REM Launch VaultBot backend from inside the Obsidian plugin.
REM Activates the virtual environment and starts main.py.
SET "VAULT2DIR=%~dp0"
cd /d "%VAULT2DIR%"
call "%VAULT2DIR%vaultbot_venv\Scripts\activate.bat"
python "%VAULT2DIR%vaultbot_backend\main.py"
