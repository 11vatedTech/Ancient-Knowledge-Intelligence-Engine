@echo off
setlocal
title The Felz Reading Room
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\aki.ps1" -Command serve
if errorlevel 1 echo The reading room could not start. Run "Finish the reading room.cmd" first.
if not defined AKI_NO_PAUSE pause
