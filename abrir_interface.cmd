@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Ambiente Python nao encontrado. Consulte a instalacao no README.md.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -X utf8 -m farmbot --config "%~dp0config.json" gui
