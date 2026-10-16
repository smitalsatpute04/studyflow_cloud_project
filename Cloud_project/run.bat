@echo off
cd /d "%~dp0"
echo Starting StudyFlow AI on http://localhost:8000 ...
python -m uvicorn main:app --reload --port 8000
pause
