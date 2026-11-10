@echo off
rem Die oeffentliche Website aktualisiert sich jetzt stuendlich automatisch ueber GitHub
rem (Workflow ".github/workflows/update.yml"). Dieses Skript oeffnet die Seite, auf der
rem man den Lauf auch sofort von Hand starten kann ("Run workflow").
start "" https://github.com/Levo006/wow-forever-ah-analytics/actions/workflows/update.yml
