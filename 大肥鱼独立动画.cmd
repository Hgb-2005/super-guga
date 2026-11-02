@echo off
chcp 65001 >nul
cd /d "%~dp0"
pythonw tools\dafish_standalone.py
