@echo off
REM Pravi MKV Prevodilac.exe u folderu dist\ (potreban Python + pip install -r requirements.txt)
python -m PyInstaller --noconfirm --onefile --windowed --name "MKV Prevodilac" --paths . app.py
echo.
echo Gotovo: dist\MKV Prevodilac.exe
pause
