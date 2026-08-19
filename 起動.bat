@echo off
chcp 65001 >nul
cd /d "%~dp0"
title mgtkit
rem Find a Python that actually runs. Checking only that the command
rem exists would match the Microsoft Store stub python.exe that Windows
rem ships on PATH, and pip would then fail for no visible reason.
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python not found. Run setup.bat to install it, then try again.
    pause
    exit /b 1
)

%PY% -c "import flask,numpy,matplotlib,openpyxl,pypdf,ezdxf" >nul 2>nul || (
  echo First-time setup: installing libraries...
  %PY% -m pip install flask numpy matplotlib openpyxl pypdf ezdxf
)

echo Starting mgtkit... (browser opens automatically)
echo To quit: close this window.
%PY% app.py
pause
