@echo off
cd /d "%~dp0"
python -m venv venv
call venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Setup complete. Double-click run.bat to start the software.
pause
