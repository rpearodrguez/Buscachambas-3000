"""
Ajustes generales que define el administrador
---------------------------------------------
Por ahora uno: el país con el que parte un usuario que todavía no tiene elecciones guardadas
(después cada usuario elige el suyo y se le recuerda). Vive en la parte "admin" del bucket
privado (admin/ajustes.json); lo escribe solo la herramienta de administración y la GUI pública
únicamente lo lee.

A diferencia de la lista de usuarios, un archivo ilegible NO es un riesgo de seguridad (solo
cambia con qué país parte alguien), así que cae a los valores por defecto en vez de cortar.
"""

import json

ARCHIVO = "ajustes.json"


def cargar(almacen) -> dict:
    texto = almacen.leer_texto(ARCHIVO)
    if not texto:
        return {}
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        return {}
    return datos if isinstance(datos, dict) else {}


def guardar(almacen, ajustes: dict) -> None:
    almacen.escribir_texto(ARCHIVO, json.dumps(ajustes, ensure_ascii=False, indent=2, sort_keys=True))


def pais_por_defecto(ajustes: dict, paises) -> str | None:
    """El país elegido por el administrador, solo si sigue siendo uno válido."""
    pais = ajustes.get("pais_por_defecto")
    return pais if pais in paises else None


def con_pais_por_defecto(ajustes: dict, pais: str, paises) -> dict:
    """Devuelve un dict nuevo con el país por defecto; no muta el original."""
    if pais not in paises:
        raise ValueError("País desconocido.")
    nuevo = dict(ajustes)
    nuevo["pais_por_defecto"] = pais
    return nuevo
