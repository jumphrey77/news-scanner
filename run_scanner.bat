@echo off
rem cd /d %~dp0
echo Starting Finviz News Scanner...
echo.
set SCANNER_UNICODE=0
python .\finviz_news_scanner.py
pause
