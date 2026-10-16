@echo off
rem OpenPlan Studio : lancement en un double-clic depuis Windows (WSL2, distribution Debian).
rem Construit le front, sert tout sur http://localhost:8000/ et ouvre le navigateur.
rem Fermer cette fenetre (ou Ctrl+C) arrete le serveur. Rien ne quitte la machine.
title OpenPlan Studio
echo OpenPlan Studio : construction du front puis lancement (30 s environ la premiere fois)...
start "" /min cmd /c "timeout /t 25 >nul && start "" http://localhost:8000/"
wsl -d Debian -- bash -lc "cd \"$(wslpath '%~dp0')\" && make start"
echo.
echo Serveur arrete. Appuyez sur une touche pour fermer.
pause >nul
