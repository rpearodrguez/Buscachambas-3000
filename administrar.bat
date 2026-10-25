@echo off
rem ------------------------------------------------------------------------------------------
rem  Menu de administracion del Job Scanner (solo para el administrador, en este equipo).
rem  No guarda ningun dato sensible ni de tu proyecto: la conexion vive en admin_local.json y el
rem  acceso a Google en las credenciales de gcloud, ambos fuera del repositorio.
rem  Tambien se puede usar directo: administrar.bat 1 .. 6   (la misma opcion del menu)
rem ------------------------------------------------------------------------------------------
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
title Administracion - Job Scanner

if not "%~1"=="" (
    set "OPCION=%~1"
    goto despachar
)

:menu
cls
echo.
echo   ============================================================
echo     Administracion del Job Scanner   (solo en este equipo)
echo   ============================================================
echo.
echo     [1] Abrir la herramienta de administracion
echo         Pantalla web local con cinco pestanas: Conexion, Correo (SMTP),
echo         Usuarios (rol, cuota y pais), Gastos de Google Cloud y Limpieza del bucket.
echo         Antes revisa la conexion con Google Cloud.
echo.
echo     [2] Revisar la conexion con Google Cloud
echo         Solo chequeos: gcloud, sesion, credenciales, bucket privado, job,
echo         secreto del correo y lista de usuarios. No cambia nada.
echo.
echo     [3] Iniciar sesion en Google Cloud
echo         Abre el navegador para autorizar a esta herramienta (una sola vez).
echo.
echo     [4] Instalar o actualizar las dependencias
echo         pip install de requirements.txt y requirements-admin.txt.
echo.
echo     [5] Correr las pruebas automaticas
echo         Verifica que el codigo sigue funcionando.
echo.
echo     [6] Que hay en la carpeta administracion
echo.
echo     [0] Salir
echo.
choice /c 1234560 /n /m "  Elige una opcion: "
set "OPCION=%errorlevel%"
if "%OPCION%"=="7" set "OPCION=0"

:despachar
if "%OPCION%"=="1" call :abrir_admin
if "%OPCION%"=="2" call :revisar
if "%OPCION%"=="3" call :login
if "%OPCION%"=="4" call :dependencias
if "%OPCION%"=="5" call :pruebas
if "%OPCION%"=="6" call :info
if "%OPCION%"=="0" goto salir
if not "%~1"=="" goto salir
echo.
pause
goto menu

:salir
endlocal
exit /b 0


rem ==========================================================================================
rem  Opcion 1: chequeos y, si todo esta bien (o lo aceptas), abre la herramienta
rem ==========================================================================================
:abrir_admin
call :preparar
if errorlevel 1 exit /b 1
echo.
echo   Abriendo la herramienta de administracion en http://localhost:8510 ...
echo   (para cerrarla: Ctrl+C en esta ventana)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'streamlit.exe'\" | Where-Object { $_.CommandLine -match 'streamlit.*administracion.admin\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul
python -m streamlit run administracion\admin.py --server.address localhost --server.port 8510
exit /b 0

rem  Prepara todo para abrir la herramienta; devuelve 0 si se puede seguir y 1 si no
:preparar
call :python_ok
if errorlevel 1 exit /b 1
call :deps_ok
if errorlevel 1 exit /b 1
python administracion\chequeo_conexion.py
if errorlevel 3 goto preparar_primera_vez
if errorlevel 2 goto preparar_login
if errorlevel 1 goto preparar_con_fallas
exit /b 0

:preparar_primera_vez
echo   Primera vez: se abrira la herramienta para que completes la pestana Conexion.
exit /b 0

:preparar_login
echo   Hay que iniciar sesion en Google Cloud para continuar.
choice /c SN /n /m "  Iniciar sesion ahora (S/N)? "
if errorlevel 2 exit /b 1
call :login
if errorlevel 1 exit /b 1
python administracion\chequeo_conexion.py
if errorlevel 3 goto preparar_primera_vez
if errorlevel 2 exit /b 1
if errorlevel 1 goto preparar_con_fallas
exit /b 0

:preparar_con_fallas
echo   Hay problemas con la conexion: algunas pestanas pueden mostrar errores.
choice /c SN /n /m "  Abrir la herramienta de todos modos (S/N)? "
if errorlevel 2 exit /b 1
exit /b 0


rem ==========================================================================================
rem  Opcion 2: solo revisar
rem ==========================================================================================
:revisar
call :python_ok
if errorlevel 1 exit /b 1
call :deps_ok
if errorlevel 1 exit /b 1
python administracion\chequeo_conexion.py
exit /b %errorlevel%


rem ==========================================================================================
rem  Opcion 3: iniciar sesion (credenciales de aplicacion de gcloud)
rem ==========================================================================================
:login
call :buscar_gcloud
if not defined GCLOUD goto login_sin_gcloud
echo.
echo   Se abrira el navegador: entra con TU cuenta de Google (la duena del proyecto) y acepta.
echo.
call "%GCLOUD%" auth application-default login
exit /b %errorlevel%

:login_sin_gcloud
echo   [!!] No se encontro gcloud. Instala Google Cloud CLI: https://cloud.google.com/sdk/docs/install
exit /b 1

:buscar_gcloud
set "GCLOUD="
for /f "delims=" %%G in ('where gcloud.cmd 2^>nul') do if not defined GCLOUD set "GCLOUD=%%G"
if not defined GCLOUD if exist "%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" set "GCLOUD=%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
exit /b 0


rem ==========================================================================================
rem  Opcion 4: dependencias
rem ==========================================================================================
:dependencias
call :python_ok
if errorlevel 1 exit /b 1
echo.
echo   Instalando dependencias (puede tardar un par de minutos)...
echo.
python -m pip install -r requirements.txt -r requirements-admin.txt
if errorlevel 1 (
    echo.
    echo   [!!] No se pudieron instalar. Revisa el mensaje de arriba.
    exit /b 1
)
echo.
echo   [OK] Dependencias listas.
exit /b 0

:python_ok
python --version >nul 2>&1
if errorlevel 1 (
    echo   [!!] No se encontro Python en el PATH. Instalalo desde python.org y reintenta.
    exit /b 1
)
exit /b 0

:deps_ok
python -c "import streamlit, requests, bs4, pandas, google.auth, google.cloud.storage, google.cloud.run_v2, google.cloud.secretmanager, google.cloud.bigquery" >nul 2>&1
if not errorlevel 1 exit /b 0
echo   [!!] Faltan dependencias para la herramienta de administracion.
choice /c SN /n /m "  Instalarlas ahora (S/N)? "
if errorlevel 2 exit /b 1
call :dependencias
exit /b %errorlevel%


rem ==========================================================================================
rem  Opcion 5: pruebas
rem ==========================================================================================
:pruebas
call :python_ok
if errorlevel 1 exit /b 1
echo.
python -m unittest discover -s tests
exit /b %errorlevel%


rem ==========================================================================================
rem  Opcion 6: que hay en la carpeta
rem ==========================================================================================
:info
echo.
echo   Carpeta administracion\
echo.
echo     admin.py             La herramienta (pantalla web local). Pestanas:
echo                            Conexion  - proyecto, region, job, bucket y secreto
echo                            Correo    - servidor, usuario y token SMTP; correo de prueba
echo                            Usuarios  - alta y edicion: rol, cuota semanal, pais por defecto
echo                            Gastos    - ultimos dias y por mes, con detalle por SKU y region
echo                            Limpieza  - revisa el bucket y borra lo que sobra (con confirmacion)
echo     chequeo_conexion.py  Los chequeos previos con Google Cloud (se puede correr solo).
echo     admin_nube.py        Operaciones en Google Cloud: SMTP del job, token en Secret
echo                          Manager, correo de prueba y consulta de gastos.
echo     gasto.py             Calculo de los gastos: totales, meses, comparaciones y detalle por SKU.
echo     gasto_grafico.py     El grafico de barras apiladas de la pestana Gastos.
echo.
echo   Tus datos personales NO estan en el repositorio: la conexion se guarda en
echo   admin_local.json y el acceso a Google en las credenciales de gcloud.
exit /b 0
