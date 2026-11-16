@echo off
setlocal
title Testing the AKI launchers
cd /d "%~dp0"
echo This runs the real launchers on Windows against small throwaway test archives in your
echo temporary folder. Your real archive, database and Library are not read or changed.
echo It takes several minutes (one test reads a 400-page synthetic book and interrupts it).
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\test-launcher.ps1"
if errorlevel 1 (
  echo.
  echo Some launcher tests failed. The report is in the test-results folder.
) else (
  echo.
  echo All launcher tests passed. The report is in the test-results folder.
)
pause
