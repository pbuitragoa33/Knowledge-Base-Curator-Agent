# Script PowerShell para ejecutar la aplicación

Write-Host "========================================"
Write-Host " Gestor de Subidas de Archivos"
Write-Host "========================================"
Write-Host ""

# Verificar si Python está instalado

try {
    python --version | Out-Null
} catch {
    Write-Host "[ERROR] Python no está instalado o no está en el PATH" -ForegroundColor Red
    Write-Host "Por favor, instala Python desde https://www.python.org/downloads/" -ForegroundColor Yellow
    Read-Host "Presiona Enter para salir"
    exit 1
}

# Instalar dependencias

Write-Host "[1/2] Instalando dependencias..." -ForegroundColor Green
pip install -r requirements.txt

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] No se pudieron instalar las dependencias" -ForegroundColor Red
    Read-Host "Presiona Enter para salir"
    exit 1
}

# Ejecutar la aplicación

Write-Host ""
Write-Host "[2/2] Iniciando la aplicación..." -ForegroundColor Green
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " La aplicación está lista en:" -ForegroundColor Cyan
Write-Host " http://localhost:5000" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

python app.py

Read-Host "Presiona Enter para salir"
