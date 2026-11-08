@echo off
title Build AI Professor Offline Study (.exe for students)
cd /d "%~dp0"
echo [0/2] Voice model for spoken questions (skipped if already downloaded)...
python get_speech_model.py
echo [1/2] Installing PyInstaller...
python -m pip install -r requirements.txt pyinstaller || goto :fail
echo [2/2] Building...  (takes a few minutes)
set "ICON="
if exist "static\favicon.ico" set "ICON=--icon static\favicon.ico"
python -m PyInstaller --noconfirm --clean --onedir --name "AI Professor Offline Study" %ICON% ^
  --add-data "templates;templates" --add-data "static;static" --add-data "data;data" ^
  --collect-data docx --collect-data pptx --collect-submodules pypdf --collect-all edge_tts ^
  --hidden-import win32com.client --hidden-import pythoncom ^
  --collect-all faster_whisper --collect-all ctranslate2 --collect-all tokenizers --collect-all vosk ^
  --exclude-module psycopg --exclude-module psycopg_pool --exclude-module tkinter ^
  offline.py || goto :fail
echo.
echo DONE. Give students the whole folder:  dist\AI Professor Offline Study\
echo They double-click "AI Professor Offline Study.exe" - no Python, no internet needed.
echo (Zip that folder to share it. Put Ollama on their laptop too for the best AI - optional.)
pause
exit /b 0
:fail
echo.
echo Something failed above. Take a screenshot and send it.
pause
exit /b 1
