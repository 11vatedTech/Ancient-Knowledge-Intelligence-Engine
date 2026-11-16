@echo off
setlocal
set STEP_FAILED=0
title Back up the AKI archive
cd /d "%~dp0"
echo This makes a recoverable copy of the archive: the database and every original scan,
echo then proves it by restoring the database to a scratch file and re-hashing every original.
echo Choose a folder on ANOTHER drive if you have one (for example E:\AKI-backup).
echo A backup on the same drive does not protect against that drive failing.
echo.
set "DEST=%~1"
if "%DEST%"=="" set /p "DEST=Backup folder: "
if "%DEST%"=="" goto failed
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\aki.ps1" -Command archive-backup -To "%DEST%"
if errorlevel 1 goto failed
echo.
echo Backup made and restore-tested. You can now run "Finish the reading room.cmd".
goto end
:failed
set STEP_FAILED=1
echo.
echo No verified backup was made. Nothing in the archive was changed.
:end
if not defined AKI_NO_PAUSE pause
if defined AKI_NO_PAUSE exit /b %STEP_FAILED%
