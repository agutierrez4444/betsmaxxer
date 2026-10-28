@echo off
REM Refresh NHL + NFL + NBA data, then open the dashboard at http://localhost:8000
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
py -m pipeline.build
py -m pipeline.nfl_build
py -m pipeline.nba_build
start "" http://localhost:8000
py -m http.server 8000 -d site
