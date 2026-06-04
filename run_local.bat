@echo off
cd /d "%~dp0"
pip install -r requirements.txt --quiet
start "" pythonw ollama_stats.pyw http://localhost:11434
