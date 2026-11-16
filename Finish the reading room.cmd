@echo off
setlocal
set STEP_FAILED=0
title Finishing the Felz Reading Room
cd /d "%~dp0"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\aki.ps1""

echo Step 1 of 5: moving the archive out of OneDrive to C:\AKI (copied and verified; nothing is deleted).
echo This needs a verified archive backup first: run "Back up the archive.cmd" if you have not.
%PS% -Command relocate
if errorlevel 1 goto failed

echo.
echo Step 2 of 5: writing readable copies of the books
%PS% -Command library
if errorlevel 1 goto failed

echo.
echo Step 3 of 5: reading every remaining book and building search. This takes hours.
echo You can close this window at any time; running this file again resumes where it stopped.
%PS% -Command finish
if errorlevel 1 goto failed

echo.
echo Step 4 of 5: copying the readable Library into the project folder for OneDrive
%PS% -Command library-sync
if errorlevel 1 goto failed

echo.
echo Step 5 of 5: opening the reading room
%PS% -Command serve
if errorlevel 1 goto failed
goto end

:failed
set STEP_FAILED=1
echo.
echo The run stopped at the step above. Nothing was deleted, and the reading room was not opened.
echo The logs are in the archive's logs folder (C:\AKI\logs once the archive has moved).
echo Fix the problem it describes, then run this file again to resume.

:end
if not defined AKI_NO_PAUSE pause
if defined AKI_NO_PAUSE exit /b %STEP_FAILED%
