@echo off
REM Script para ejecutar la aplicación en Windows

echo.
echo ========================================
echo  Gestor de Subidas de Archivos
echo ========================================
echo.

REM Verificar si Python está instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no está instalado o no está en el PATH
    echo Por favor, instala Python desde https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Instalar dependencias
echo [1/2] Instalando dependencias...
pip install -r requirements.txt

if errorlevel 1 (
    echo [ERROR] No se pudieron instalar las dependencias
    pause
    exit /b 1
)

REM Ejecutar la aplicación
echo.
echo [2/2] Iniciando la aplicación...
echo.
echo ========================================
echo  http://localhost:5000
echo ========================================
echo.

python app.py

pause
