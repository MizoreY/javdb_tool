@echo off
chcp 65001 >NUL 2>&1
cd /d "%~dp0"
python javdb_rating.py
