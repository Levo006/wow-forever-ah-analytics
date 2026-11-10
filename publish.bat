@echo off
rem Aktualisiert die oeffentliche Seite auf GitHub Pages:
rem 1. Auctionator-Daten importieren  2. statischen Export nach docs\ schreiben
rem 3. docs\ committen und pushen (GitHub Pages veroeffentlicht automatisch)
chcp 65001 >nul
cd /d "%~dp0"
python run.py import-auctionator || goto :err
python run.py export --repo-url https://github.com/Levo006/wow-forever-ah-analytics || goto :err
git add docs || goto :err
git commit -m "Daten aktualisiert" || echo Keine Aenderungen.
git push || goto :err
echo.
echo Fertig. Die Seite ist in 1-2 Minuten aktualisiert:
echo https://levo006.github.io/wow-forever-ah-analytics/
pause
exit /b 0
:err
echo Fehler - siehe Ausgabe oben.
pause
exit /b 1
