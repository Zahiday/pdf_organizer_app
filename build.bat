@echo off
title PDF Organizer Builder
color 0A

cd /d "%~dp0"

echo ==========================================
echo        PDF Organizer Build
echo ==========================================
echo.

echo [1/4] Pruefe Python...
"C:\Users\Aryobi\AppData\Local\Python\pythoncore-3.14-64\python.exe" --version
if errorlevel 1 (
    echo.
    echo Python wurde nicht gefunden.
    pause
    exit /b
)

echo.
echo [2/4] Installiere/Aktualisiere PyInstaller...
"C:\Users\Aryobi\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m pip install --upgrade pyinstaller

echo.
echo [3/4] Alte Buildordner loeschen...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist PDF-Organizer.spec del /f /q PDF-Organizer.spec

echo.
echo [4/4] Erstelle EXE...

"C:\Users\Aryobi\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m PyInstaller ^
--onefile ^
--windowed ^
--clean ^
--noconfirm ^
--name "PDF-Organizer" ^
app.py

echo.
echo ==========================================
echo Build abgeschlossen.
echo.

if exist "dist\PDF-Organizer.exe" (
    echo EXE erfolgreich erstellt:
    echo.
    echo %CD%\dist\PDF-Organizer.exe
) else (
    echo Fehler: Es wurde keine EXE erstellt.
)

echo.
pause