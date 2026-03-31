@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ==========================================
echo   ScanMatic AutoTech Learning
echo ==========================================
echo.

set "PYEXE="

if exist ".venv\Scripts\python.exe" (
    set "PYEXE=.venv\Scripts\python.exe"
) else (
    echo [1/4] Creando entorno virtual...
    py -3.12 -m venv .venv >nul 2>nul
    if errorlevel 1 (
        py -3.11 -m venv .venv >nul 2>nul
    )
    if errorlevel 1 (
        py -3.10 -m venv .venv >nul 2>nul
    )
    if errorlevel 1 (
        py -3 -m venv .venv >nul 2>nul
    )
    if errorlevel 1 (
        python -m venv .venv >nul 2>nul
    )
    if exist ".venv\Scripts\python.exe" (
        set "PYEXE=.venv\Scripts\python.exe"
    ) else (
        echo ERROR: No se pudo crear el entorno virtual.
        echo Instala Python para Windows con pip habilitado.
        pause
        exit /b 1
    )
)

echo [2/4] Inicio directo (sin verificacion de dependencias).

set "LOCAL_IP="

for /f "tokens=*" %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue ^| Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*' -and $_.PrefixOrigin -ne 'WellKnown' }; if($ips){ ($ips ^| Select-Object -ExpandProperty IPAddress -First 1) }"') do (
    set "LOCAL_IP=%%I"
)

if not defined LOCAL_IP (
    for /f "tokens=2 delims=:" %%I in ('ipconfig ^| findstr /I "IPv4"') do (
        set "LOCAL_IP=%%I"
        goto :trimip
    )
)

:trimip
if defined LOCAL_IP (
    set "LOCAL_IP=%LOCAL_IP: =%"
) else (
    set "LOCAL_IP=127.0.0.1"
)

echo [4/4] Iniciando servidor...
echo.
echo Instructor local:
echo   http://127.0.0.1:8000/instructor
echo.
echo Estudiante local:
echo   http://127.0.0.1:8000/student
echo.
echo Estudiante en la red:
echo   http://%LOCAL_IP%:8000/student
echo.

call "%PYEXE%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --ws wsproto
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo El servidor se cerro con codigo %EXITCODE%.
)
pause
exit /b %EXITCODE%
