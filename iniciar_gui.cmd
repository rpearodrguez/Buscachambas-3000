@echo off
cd /d "%~dp0"

echo Verificando dependencias...
python -c "import requests, bs4, streamlit, pandas, pypdf, docx, pyperclip" 2>nul
if errorlevel 1 (
    echo Faltan dependencias, instalando desde requirements.txt...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: no se pudieron instalar las dependencias. Revisa el mensaje de arriba.
        pause
        exit /b 1
    )
)

echo Abriendo Job Scanner...
streamlit run gui.py
pause
