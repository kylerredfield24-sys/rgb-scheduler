@echo off
setlocal
cd /d "%~dp0"
title Building RGB Scheduler

echo Closing RGB Scheduler if it's running...
taskkill /IM "RGB Scheduler.exe" /F >nul 2>&1
timeout /t 1 /nobreak >nul

where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")

rem Use the newest rgb_scheduler*.py in this folder (downloads often get renamed,
rem like "rgb_scheduler (1).py") and save it as rgb_scheduler.py
set "SRC="
for /f "delims=" %%F in ('dir /b /o:-d "rgb_scheduler*.py" 2^>nul') do if not defined SRC set "SRC=%%F"
if not defined SRC (
  echo.
  echo Can't find rgb_scheduler.py in %~dp0
  echo Put rgb_scheduler.py in the same folder as this build.bat, then run it again.
  goto :fail
)
if /i not "%SRC%"=="rgb_scheduler.py" (
  echo Using "%SRC%" as rgb_scheduler.py
  copy /y "%SRC%" "rgb_scheduler.py" >nul
)

echo.
echo Installing what the app needs...
%PY% -m pip install --upgrade pip >nul
%PY% -m pip install --upgrade pyinstaller customtkinter pystray pillow numpy psutil pywinauto comtypes
if errorlevel 1 goto :fail

echo.
echo Installing the screen text reader (optional)...
%PY% -m pip install --upgrade winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Globalization >nul 2>&1
if errorlevel 1 echo   (text reader didn't install - the app still works without it)

echo.
echo Making the icon...
%PY% rgb_scheduler.py --make-icon
if errorlevel 1 goto :fail

echo.
echo Building RGB Scheduler.exe...
if exist build rmdir /s /q build
if exist "dist\RGB Scheduler.exe" del /f /q "dist\RGB Scheduler.exe"
%PY% -m PyInstaller --noconfirm --onefile --windowed --name "RGB Scheduler" --icon icon.ico ^
  --collect-all customtkinter --collect-all winrt ^
  --hidden-import pystray._win32 --hidden-import comtypes.stream ^
  --hidden-import pywinauto.controls.uia_controls --hidden-import pywinauto.controls.uiawrapper ^
  rgb_scheduler.py
if errorlevel 1 goto :fail

echo.
echo Done! Your app is here:
echo   %~dp0dist\RGB Scheduler.exe
echo.
echo The first time you open it, click Yes once. After that it opens as admin with no prompt.
explorer "%~dp0dist"
pause
exit /b 0

:fail
echo.
echo Something went wrong. Copy the red text above and send it to Claude.
pause
exit /b 1
