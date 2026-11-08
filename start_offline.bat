@echo off
title AI Professor - Offline Study
cd /d "%~dp0"
python -c "import flask, pypdf, docx, pptx" >nul 2>&1
if errorlevel 1 (
    echo First time: installing what the app needs ^(internet needed once^)...
    python -m pip install -r requirements.txt
)
python offline.py
pause
