"""
Lista de usuarios autorizados
-----------------------------
Quién puede entrar a la GUI, con qué rol y qué cuota semanal de scans tiene.
Vive como un archivo JSON en la parte "admin" del bucket privado (nunca en el
repo: son correos de personas). Lo edita solo la herramienta de
administración (admin.py, que corre en la PC del administrador); la GUI
pública solo lo lee para decidir si alguien puede entrar.

Todo son funciones puras sobre un dict, para poder probarlas sin la nube:
    datos = cargar(almacen)      -> {"usuarios": {correo: {"rol", "cuota_semanal"}}}
    datos = agregar(datos, ...)  -> devuelve un dict nuevo, no muta el original
    guardar(almacen, datos)
"""

import copy
import json
import re

ARCHIVO = "usuarios.json"
ROLES = ("usuario", "admin")
CUOTA_SEMANAL_POR_DEFECTO = 1
CUOTA_MAXIMA = 50

_CORREO = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)+$")


class ErrorUsuarios(ValueError):
    """Entrada inválida; el mensaje se puede mostrar tal cual al administrador."""


def normalizar_correo(correo: str) -> str:
    return (correo or "").strip().lower()


def validar_correo(correo: str) -> str:
    """Devuelve el correo normalizado o lanza ErrorUsuarios."""
    c = normalizar_correo(correo)
    if len(c) > 254 or not _CORREO.match(c):
        raise ErrorUsuarios("El correo no es válido.")
    return c


def vacio() -> dict:
    return {"usuarios": {}}


def cargar(almacen) -> dict:
    texto = almacen.leer_texto(ARCHIVO)
    if texto is None:
        return vacio()
    try:
        datos = json.loads(texto)
        usuarios = datos["usuarios"]
        if not isinstance(usuarios, dict):
            raise TypeError
    except (json.JSONDecodeError, KeyError, TypeError):
        # Un archivo ilegible no debe abrirle la puerta a nadie ni borrarse
        # en silencio: se corta con un error claro.
        raise ErrorUsuarios("El archivo de usuarios está dañado; revísalo antes de seguir.") from None
    return {"usuarios": {normalizar_correo(c): dict(v) for c, v in usuarios.items()}}


def guardar(almacen, datos: dict) -> None:
    almacen.escribir_texto(ARCHIVO, json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True))


def _validar_cuota(cuota) -> int:
    try:
        cuota = int(cuota)
    except (TypeError, ValueError):
        raise ErrorUsuarios("La cuota debe ser un número entero.") from None
    if not 0 <= cuota <= CUOTA_MAXIMA:
        raise ErrorUsuarios(f"La cuota semanal debe estar entre 0 y {CUOTA_MAXIMA}.")
    return cuota


def agregar(datos: dict, correo: str, rol: str = "usuario", cuota_semanal: int = CUOTA_SEMANAL_POR_DEFECTO) -> dict:
    """Agrega o actualiza un usuario. Devuelve un dict nuevo."""
    c = validar_correo(correo)
    if rol not in ROLES:
        raise ErrorUsuarios("Rol desconocido.")
    nuevo = copy.deepcopy(datos)
    nuevo["usuarios"][c] = {"rol": rol, "cuota_semanal": _validar_cuota(cuota_semanal)}
    return nuevo


def quitar(datos: dict, correo: str) -> dict:
    """Quita un usuario. No deja la lista sin ningún administrador."""
    c = normalizar_correo(correo)
    if c not in datos["usuarios"]:
        raise ErrorUsuarios("Ese usuario no está en la lista.")
    nuevo = copy.deepcopy(datos)
    del nuevo["usuarios"][c]
    if datos["usuarios"][c].get("rol") == "admin" and not any(u.get("rol") == "admin" for u in nuevo["usuarios"].values()):
        raise ErrorUsuarios("No se puede quitar al último administrador.")
    return nuevo


def info(datos: dict, correo: str) -> dict | None:
    """Datos del usuario, o None si no está autorizado."""
    return datos["usuarios"].get(normalizar_correo(correo))


def es_permitido(datos: dict, correo: str) -> bool:
    return info(datos, correo) is not None


def es_admin(datos: dict, correo: str) -> bool:
    u = info(datos, correo)
    return bool(u) and u.get("rol") == "admin"
