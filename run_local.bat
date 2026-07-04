@echo off
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt --quiet
start "" pyw -3 ollama_stats.pyw http://localhost:11434
