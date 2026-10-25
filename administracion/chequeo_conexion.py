"""
Chequeos previos de la conexión con Google Cloud (para administrar.bat y la herramienta de administración)
----------------------------------------------------------------------------------------------------------
Revisa, de lo más básico a lo más específico: gcloud instalado, sesión de gcloud, credenciales de
aplicación (las que usa administracion/admin.py), la conexión guardada en admin_local.json, el bucket
(que sea privado), el job, el secreto del token SMTP y la lista de usuarios. Solo lectura: no cambia nada
en la nube, y no imprime ningún secreto ni token.

    python administracion/chequeo_conexion.py

Código de salida: 0 todo bien · 1 algo falló · 2 falta iniciar sesión (o venció) y se arregla con
`gcloud auth application-default login` · 3 solo falta completar la conexión (primera vez).
"""

import os
import shutil
import subprocess
import sys

# Punto de entrada en una subcarpeta: la raíz del repo va en sys.path para importar los paquetes hermanos.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK, FALLA, AVISO = "[OK]", "[!!]", "[--]"


def linea(estado: str, texto: str, detalle: str = "") -> None:
    print(f"  {estado} {texto}" + (f"  ->  {detalle}" if detalle else ""))


def buscar_gcloud() -> str | None:
    ruta = shutil.which("gcloud")
    if ruta:
        return ruta
    candidato = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Cloud SDK", "google-cloud-sdk", "bin", "gcloud.cmd")
    return candidato if os.path.exists(candidato) else None


def correr(comando: list[str], timeout: int = 40) -> tuple[int, str]:
    try:
        r = subprocess.run(comando, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def chequear() -> int:
    fallas = 0
    necesita_login = False
    falta_conexion = False

    print("\n  Revisando la conexion con Google Cloud...\n")

    # 1) gcloud instalado
    gcloud = buscar_gcloud()
    if gcloud:
        _, version = correr([gcloud, "--version"])
        linea(OK, "gcloud instalado", version.splitlines()[0] if version else "")
    else:
        linea(FALLA, "gcloud no esta instalado", "instala Google Cloud CLI: cloud.google.com/sdk/docs/install")
        fallas += 1

    # 2) sesión de gcloud
    if gcloud:
        _, cuenta = correr([gcloud, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"])
        if cuenta:
            linea(OK, "sesion de gcloud activa", cuenta.splitlines()[0])
        else:
            linea(AVISO, "gcloud sin sesion activa", "se resuelve con la opcion de iniciar sesion")

    # 3) credenciales de aplicación (las que usa admin.py)
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError, RefreshError
        from google.auth.transport.requests import Request
    except ImportError:
        linea(FALLA, "faltan las librerias de Google", "instala las dependencias (opcion 4 del menu)")
        return 1

    credenciales = None
    try:
        credenciales, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credenciales.refresh(Request())
        linea(OK, "credenciales de aplicacion validas")
    except DefaultCredentialsError:
        linea(FALLA, "no hay credenciales de aplicacion", "inicia sesion (opcion 3 del menu)")
        necesita_login = True
        credenciales = None
    except RefreshError:
        linea(FALLA, "las credenciales de aplicacion vencieron o fueron revocadas", "vuelve a iniciar sesion (opcion 3)")
        necesita_login = True
        credenciales = None
    except Exception as e:  # red caida, etc.
        linea(FALLA, "no se pudieron validar las credenciales", type(e).__name__)
        fallas += 1
        credenciales = None

    # 4) conexión guardada
    from administracion import admin_nube

    cfg = admin_nube.cargar_config_local()
    if cfg:
        linea(OK, f"conexion guardada ({admin_nube.CONFIG_LOCAL})")
    else:
        linea(AVISO, f"falta {admin_nube.CONFIG_LOCAL}", "primera vez: abre la herramienta y completa la pestana Conexion")
        falta_conexion = True  # no es un fallo: es lo que hay que hacer la primera vez

    # 5..9) lo que se consulta en la nube necesita credenciales y conexión
    if credenciales is not None and cfg:
        credenciales = admin_nube.credenciales_admin(cfg)

        try:
            from comun import almacen as almacenamiento

            admin_almacen = almacenamiento.crear_almacen(admin_nube.destino_admin(cfg), credenciales=credenciales)
            linea(OK, "bucket accesible y privado (acceso publico bloqueado)")
        except Exception as e:
            linea(FALLA, "no se pudo verificar el bucket", type(e).__name__)
            fallas += 1
            admin_almacen = None

        try:
            smtp = admin_nube.leer_smtp_del_job(cfg, credenciales)
            linea(OK, "job encontrado", f"correo: {smtp['host'] or 'SMTP sin configurar'}")
            if not smtp["host"]:
                linea(AVISO, "el job no tiene servidor SMTP", "configuralo en la pestana Correo")
        except Exception as e:
            linea(FALLA, "no se pudo leer el job", type(e).__name__)
            fallas += 1

        try:
            from google.cloud import secretmanager

            cliente = secretmanager.SecretManagerServiceClient(credentials=credenciales)
            padre = f"projects/{cfg['project']}/secrets/{cfg['secret']}"
            activas = [v for v in cliente.list_secret_versions(request={"parent": padre}) if v.state.name == "ENABLED"]
            if activas:
                linea(OK, "secreto del token SMTP presente", f"{len(activas)} version(es) activa(s)")
            else:
                linea(AVISO, "el secreto no tiene versiones activas", "guarda el token en la pestana Correo")
        except Exception as e:
            linea(FALLA, "no se pudo consultar el secreto", type(e).__name__)
            fallas += 1

        if admin_almacen is not None:
            try:
                from comun import usuarios

                datos = usuarios.cargar(admin_almacen)
                admins = sum(1 for u in datos["usuarios"].values() if u.get("rol") == "admin")
                if admins:
                    linea(OK, "lista de usuarios", f"{len(datos['usuarios'])} usuario(s), {admins} administrador(es)")
                else:
                    linea(AVISO, "la lista de usuarios no tiene administradores", "agrega el tuyo en la pestana Usuarios")
            except Exception as e:
                linea(FALLA, "no se pudo leer la lista de usuarios", type(e).__name__)
                fallas += 1

        if cfg.get("billing_tabla"):
            linea(OK, "tabla de facturacion configurada (pestana Gastos)")
        else:
            linea(AVISO, "sin tabla de facturacion", "opcional: pegala en Conexion para ver los gastos")

    print()
    if necesita_login:
        print("  Resultado: falta iniciar sesion en Google Cloud.\n")
        return 2
    if fallas:
        print(f"  Resultado: {fallas} problema(s) por resolver.\n")
        return 1
    if falta_conexion:
        print("  Resultado: las credenciales estan bien; falta completar la conexion (primera vez).\n")
        return 3
    print("  Resultado: todo en orden.\n")
    return 0


if __name__ == "__main__":
    sys.exit(chequear())
