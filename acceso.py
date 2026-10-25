"""
Decisiones de acceso, cuota y candados de la GUI en la nube
-----------------------------------------------------------
Lógica pura (sin Streamlit) para poder probarla: quién entra, cuántos scans
le quedan, y los candados que evitan lanzar dos scans a la vez.

Todo falla cerrado: sin lista de usuarios, con el correo sin verificar o con
cualquier duda, no se entra / no se lanza.
"""

import json

import lanzador
import usuarios

VENTANA_SEMANA = 7 * 24 * 3600
USO_ARCHIVO = "uso.json"
MAX_USOS_GUARDADOS = 200

LOCK_ARCHIVO = "lanzando.lock"
LOCK_TTL = 60  # segundos: el candado solo cubre la ventana entre el clic y el estado "corriendo"

MAX_SIMULTANEOS_POR_DEFECTO = 2
STATUS_ARCHIVO = "scan_status.json"


# --- quién entra ---------------------------------------------------------------

def decidir_acceso(email, email_verificado, datos: dict) -> tuple[str, dict | None]:
    """("ok", info) o (motivo, None). Motivos: "sin_correo", "no_verificado",
    "no_autorizado". Los mensajes para el usuario no distinguen entre "no
    estás en la lista" y "la lista está vacía": no se revela quién está."""
    if not email:
        return "sin_correo", None
    if email_verificado is not True:
        return "no_verificado", None
    info = usuarios.info(datos, email)
    if info is None:
        return "no_autorizado", None
    return "ok", info


# --- cuota semanal ---------------------------------------------------------------

def cargar_usos(almacen) -> list[float]:
    texto = almacen.leer_texto(USO_ARCHIVO)
    if texto is None:
        return []
    try:
        usos = json.loads(texto)
        return sorted(float(t) for t in usos)
    except (json.JSONDecodeError, TypeError, ValueError):
        # Un registro dañado no debe regalar scans ilimitados: se trata como
        # si hubiera usado toda la cuota ahora mismo.
        return None


def registrar_uso(almacen, ahora: float) -> None:
    usos = cargar_usos(almacen) or []
    usos.append(ahora)
    almacen.escribir_texto(USO_ARCHIVO, json.dumps(usos[-MAX_USOS_GUARDADOS:]))


def evaluar_cuota(info: dict, usos: list[float] | None, ahora: float) -> tuple[bool, str | None, float | None]:
    """(puede, motivo, cuando_vuelve_a_poder). Ventana móvil de 7 días. Los
    administradores no tienen tope. Un registro ilegible (usos=None) bloquea."""
    if info.get("rol") == "admin":
        return True, None, None
    cuota = int(info.get("cuota_semanal", usuarios.CUOTA_SEMANAL_POR_DEFECTO))
    if cuota <= 0:
        return False, "Tu cuota semanal de escaneos es 0. Pídele al administrador que la ajuste.", None
    if usos is None:
        return False, "No se pudo verificar tu uso reciente. Inténtalo más tarde o avisa al administrador.", None
    recientes = [t for t in usos if ahora - t < VENTANA_SEMANA]
    if len(recientes) < cuota:
        return True, None, None
    vuelve = sorted(recientes)[len(recientes) - cuota] + VENTANA_SEMANA
    return False, "Ya usaste tu cuota de escaneos de esta semana.", vuelve


# --- candado por usuario ------------------------------------------------------------

def adquirir_lock(almacen, ahora: float) -> bool:
    """Candado atómico con vencimiento: evita que dos clics seguidos (o dos
    pestañas) lancen dos scans. Un candado viejo (la GUI murió antes de
    liberarlo) se descarta."""
    contenido = json.dumps({"ts": ahora}).encode("utf-8")
    if almacen.crear_si_no_existe(LOCK_ARCHIVO, contenido):
        return True
    try:
        ts = json.loads(almacen.leer_texto(LOCK_ARCHIVO) or "{}").get("ts", 0)
    except (json.JSONDecodeError, AttributeError):
        ts = 0
    if ahora - float(ts) < LOCK_TTL:
        return False
    almacen.borrar(LOCK_ARCHIVO)
    return almacen.crear_si_no_existe(LOCK_ARCHIVO, contenido)


def liberar_lock(almacen) -> None:
    almacen.borrar(LOCK_ARCHIVO)


# --- tope global de escaneos simultáneos -------------------------------------------------

def contar_scans_activos(almacen_raiz, ahora: float) -> int:
    """Cuántos scans (de todos los usuarios) están corriendo ahora: los que
    tienen "corriendo" y siguen dando señales. Requiere el almacén de la
    raíz del bucket (ve las carpetas de todos), nunca el de un usuario."""
    activos = 0
    for nombre in almacen_raiz.listar(f"{usuarios_prefijo()}/"):
        if not nombre.endswith(f"/{STATUS_ARCHIVO}"):
            continue
        try:
            estado = json.loads(almacen_raiz.leer_texto(nombre) or "{}")
        except json.JSONDecodeError:
            continue
        if estado.get("corriendo") and not lanzador.scan_sin_latido(estado, ahora):
            activos += 1
    return activos


def usuarios_prefijo() -> str:
    import almacen as almacenamiento
    return almacenamiento.PREFIJO_USUARIOS
