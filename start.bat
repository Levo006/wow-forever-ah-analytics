@echo off
rem Startet die Website mit den echten Daten (data\ah.sqlite).
rem Vorher werden die aktuellen Auctionator-Daten importiert.
chcp 65001 >nul
cd /d "%~dp0"
python run.py import-auctionator
start "" http://127.0.0.1:8765
python run.py serve %*
pause
