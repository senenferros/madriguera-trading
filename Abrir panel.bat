@echo off
cd /d "%~dp0"
title La Madriguera Trading - panel
if not exist .venv\Scripts\python.exe (
  echo Creando el entorno de Python...
  python -m venv .venv
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
.venv\Scripts\python.exe app.py
if errorlevel 1 pause
