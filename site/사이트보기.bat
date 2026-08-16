@echo off
chcp 65001 > nul
title ARGUS site
cd /d "%~dp0out"

if not exist index.html (
  echo.
  echo   [!] site\out folder is empty - build first:
  echo       cd /d "%~dp0"
  echo       npm run data ^&^& npm run build
  echo.
  pause
  exit /b 1
)

echo.
echo   ARGUS site  ->  http://localhost:8000
echo   Press Ctrl+C in this window to stop.
echo.

start "" http://localhost:8000
"C:\Users\Juhwankim\miniconda3\python.exe" -m http.server 8000
