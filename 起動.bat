@echo off
chcp 65001 >nul
cd /d "%~dp0"
title mgtkit
where py >nul 2>nul && (set PY=py) || (set PY=python)

%PY% -c "import flask,numpy,matplotlib,openpyxl,pypdf,ezdxf" >nul 2>nul || (
  echo First-time setup: installing libraries...
  %PY% -m pip install flask numpy matplotlib openpyxl pypdf ezdxf
)

echo Starting mgtkit... (browser opens automatically)
echo To quit: close this window.
%PY% app.py
pause
