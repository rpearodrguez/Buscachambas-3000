"""
Limpieza del bucket: qué se puede borrar sin riesgo, y la guarda que impide borrar lo que no
------------------------------------------------------------------------------------------
Lo que se acumula en el bucket y que sí conviene limpiar:
  - carpetas de usuarios que ya no están en la lista (o de pruebas viejas): datos de nadie,
  - candados y banderas temporales que quedaron colgados (lanzando.lock, detener.flag),
  - archivos de funciones que ya no existen (admin/ajustes.json),
  - estados de scans que murieron sin cerrarse (scan_status.json con "corriendo" hace días).

Todo lo demás NO se toca nunca: la lista de usuarios, y cualquier archivo de un usuario vigente
(resultados, keywords, preferencias, uso). Lo borrable se decide en es_borrable(), que es la única
puerta: ejecutar() la consulta para CADA nombre antes de borrar nada, y si uno solo no pasa, no
borra ninguno. Son funciones puras sobre listas de objetos para poder probarlas sin la nube.
"""

import json

from comun import almacen as almacenamiento
from comun import lanzador, usuarios

PREFIJO_USUARIOS = almacenamiento.PREFIJO_USUARIOS + "/"
TEMPORALES = ("lanzando.lock", "detener.flag")  # archivos efímeros dentro de la carpeta de un usuario
OBSOLETOS_ADMIN = ("admin/ajustes.json",)  # de una función que se quitó
PROTEGIDOS = ("admin/" + usuarios.ARCHIVO,)  # nunca se borra, pase lo que pase
UMBRAL_TEMPORAL = 24 * 3600  # un candado o bandera con más de un día es basura
UMBRAL_COLGADO = 24 * 3600  # un scan "corriendo" sin dar señales hace más de un día murió
GRATIS_BYTES = 5 * 1024 ** 3  # los 5 GB de Cloud Storage del free tier


class ErrorLimpieza(ValueError):
    """Un nombre que no se puede borrar; el mensaje es genérico a propósito (no revela rutas)."""


def ids_vigentes(datos_usuarios: dict) -> dict[str, str]:
    """{id de carpeta: correo} de los usuarios que siguen en la lista."""
    return {almacenamiento.id_usuario(c): c for c in datos_usuarios["usuarios"]}


def id_de_carpeta(nombre: str) -> str | None:
    """"users/<id>/archivo" -> "<id>"; None si no tiene esa forma o intenta salirse (.., vacío)."""
    if not nombre.startswith(PREFIJO_USUARIOS) or ".." in nombre or "//" in nombre or "\\" in nombre:
        return None
    partes = nombre[len(PREFIJO_USUARIOS):].split("/")
    return partes[0] if len(partes) >= 2 and partes[0] and all(partes) else None


def es_borrable(nombre: str, vigentes) -> bool:
    """La única puerta de la limpieza. True solo si el archivo es: (1) de una carpeta de un usuario que
    YA NO está en la lista, (2) un temporal (lock/flag) de cualquier carpeta, o (3) un archivo obsoleto
    conocido de admin/. La lista de usuarios y los archivos de usuarios vigentes dan siempre False."""
    if nombre in PROTEGIDOS:
        return False
    if nombre in OBSOLETOS_ADMIN:
        return True
    carpeta = id_de_carpeta(nombre)
    if carpeta is None:
        return False
    if nombre.rsplit("/", 1)[-1] in TEMPORALES and nombre.count("/") == 2:
        return True
    return carpeta not in vigentes


def analizar(objetos: list[dict], datos_usuarios: dict, ahora: float) -> dict:
    """Mira el listado completo del bucket (con tamaño y fecha) y separa lo limpiable de lo que no."""
    vigentes = ids_vigentes(datos_usuarios)
    carpetas: dict[str, dict] = {}
    temporales, obsoletos = [], []
    total_bytes = 0
    for o in objetos:
        total_bytes += o["bytes"]
        nombre = o["nombre"]
        if nombre in OBSOLETOS_ADMIN:
            obsoletos.append(nombre)
        carpeta = id_de_carpeta(nombre)
        if carpeta is None:
            continue
        c = carpetas.setdefault(carpeta, {"id": carpeta, "objetos": 0, "bytes": 0, "ultima_actividad": 0.0, "nombres": []})
        c["objetos"] += 1
        c["bytes"] += o["bytes"]
        c["ultima_actividad"] = max(c["ultima_actividad"], o["actualizado"])
        c["nombres"].append(nombre)
        if nombre.rsplit("/", 1)[-1] in TEMPORALES and nombre.count("/") == 2 and ahora - o["actualizado"] > UMBRAL_TEMPORAL:
            temporales.append(nombre)
    filas_carpetas = []
    for c in sorted(carpetas.values(), key=lambda c: -c["bytes"]):
        c["vigente"] = c["id"] in vigentes
        c["correo"] = vigentes.get(c["id"])
        filas_carpetas.append(c)
    return {
        "objetos": len(objetos), "bytes": total_bytes, "porcentaje_gratis": total_bytes / GRATIS_BYTES,
        "carpetas": filas_carpetas,
        "huerfanas": [c for c in filas_carpetas if not c["vigente"]],
        "temporales_vencidos": sorted(temporales),
        "obsoletos": sorted(obsoletos),
    }


def estados_colgados(almacen_raiz, analisis: dict, ahora: float) -> list[str]:
    """scan_status.json de usuarios vigentes que dicen "corriendo" pero no dan señales hace más de un día:
    el job murió sin cerrar su estado. Lee cada archivo; los ilegibles se ignoran (no se borran a ciegas)."""
    colgados = []
    for c in analisis["carpetas"]:
        nombre = f"{PREFIJO_USUARIOS}{c['id']}/scan_status.json"
        if nombre not in c["nombres"]:
            continue
        try:
            estado = json.loads(almacen_raiz.leer_texto(nombre) or "{}")
        except (json.JSONDecodeError, almacenamiento.ErrorAlmacen):
            continue
        if estado.get("corriendo") and lanzador.scan_sin_latido(estado, ahora, UMBRAL_COLGADO):
            colgados.append(nombre)
    return sorted(colgados)


def ejecutar(almacen_raiz, nombres: list[str], vigentes) -> int:
    """Borra `nombres`, que deben pasar TODOS por es_borrable(); si alguno no pasa, no borra ninguno
    (el error no dice cuál ni su ruta). Devuelve cuántos borró."""
    nombres = list(dict.fromkeys(nombres))
    if not all(es_borrable(n, vigentes) for n in nombres):
        raise ErrorLimpieza("La lista de archivos a borrar incluye algo que no se puede borrar; no se borró nada.")
    for n in nombres:
        almacen_raiz.borrar(n)
    return len(nombres)
