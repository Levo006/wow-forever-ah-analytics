@echo off
rem Aktualisiert die oeffentliche Seite auf GitHub Pages:
rem 1. Auctionator-Daten importieren (Scans aus dem Spiel)
rem 2. Battle.net-Snapshot holen (nur wenn config.toml einen API-Zugang enthaelt)
rem 3. Website exportieren und auf den Zweig gh-pages hochladen
chcp 65001 >nul
cd /d "%~dp0"
python run.py import-auctionator
python run.py collect
python run.py publish || goto :err
echo.
echo Fertig. Die Seite ist in 1-2 Minuten aktualisiert.
pause
exit /b 0
:err
echo Fehler - siehe Ausgabe oben.
pause
exit /b 1
