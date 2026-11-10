@echo off
rem Startet die Website mit Beispieldaten (data\demo.sqlite, wird beim ersten Start erzeugt).
chcp 65001 >nul
cd /d "%~dp0"
if not exist data\demo.sqlite python run.py --db data\demo.sqlite demo
start "" http://127.0.0.1:8766
python run.py --db data\demo.sqlite serve --port 8766
pause
