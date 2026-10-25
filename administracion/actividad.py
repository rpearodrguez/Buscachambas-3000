"""
Actividad: cuántos escaneos se han hecho, por día, semana, mes y año (lógica pura del panel "Actividad")
-------------------------------------------------------------------------------------------------------
Cada vez que la GUI en la nube lanza un escaneo anota la hora en el archivo de uso del usuario
(users/<id>/uso.json; ver comun/acceso.py). Aquí se juntan esos registros de todos los usuarios y se
cuentan por período. Las fechas se agrupan en la hora local de quien mira (la de la PC del
administrador), no en UTC, para que un escaneo de la noche no caiga en el día siguiente.

Solo cuenta los escaneos lanzados desde la GUI en la nube: los que corres desde tu PC no pasan por ahí.
Sin dependencias de la nube ni de Streamlit, para poder probarlo.
"""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from comun import almacen as almacenamiento

GRANULARIDADES = ("dia", "semana", "mes", "anio")
NOMBRES = {"dia": "Día", "semana": "Semana", "mes": "Mes", "anio": "Año"}
VENTANA_POR_DEFECTO = {"dia": 30, "semana": 12, "mes": 12, "anio": 5}  # cuántos períodos se muestran
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
SIN_USUARIO = "(ya no está en la lista)"


def leer_usos(almacen_raiz) -> dict[str, list[float]]:
    """{id de carpeta: horas de sus escaneos} de TODOS los usuarios, leyendo users/<id>/uso.json. Un archivo
    ilegible o con basura se ignora: un registro dañado no debe tumbar el panel."""
    usos = {}
    for nombre in almacen_raiz.listar(almacenamiento.PREFIJO_USUARIOS + "/"):
        partes = nombre.split("/")
        if len(partes) != 3 or partes[2] != "uso.json":
            continue
        try:
            datos = json.loads(almacen_raiz.leer_texto(nombre) or "[]")
            usos[partes[1]] = sorted(float(t) for t in datos)
        except (json.JSONDecodeError, TypeError, ValueError, almacenamiento.ErrorAlmacen):
            continue
    return usos


def clave_de(momento: datetime, granularidad: str) -> str:
    """El período al que pertenece un instante: día "2026-10-25", semana (el lunes que la abre)
    "2026-10-19", mes "2026-10" o año "2026"."""
    d = momento.date()
    if granularidad == "dia":
        return d.isoformat()
    if granularidad == "semana":
        return (d - timedelta(days=d.weekday())).isoformat()
    if granularidad == "mes":
        return f"{d.year:04d}-{d.month:02d}"
    if granularidad == "anio":
        return f"{d.year:04d}"
    raise ValueError("granularidad desconocida")


def etiqueta_de(clave: str, granularidad: str) -> str:
    """Texto del eje, ya formateado (sin depender de zonas horarias ni del idioma del navegador)."""
    if granularidad in ("dia", "semana"):
        anio, mes, dia = clave.split("-")
        texto = f"{int(dia):02d} {MESES[int(mes) - 1]}"
        return f"sem. {texto}" if granularidad == "semana" else texto
    if granularidad == "mes":
        anio, mes = clave.split("-")
        return f"{MESES[int(mes) - 1]} {anio}"
    return clave


def periodos(granularidad: str, cantidad: int, hasta: date) -> list[str]:
    """Las claves de los últimos `cantidad` períodos terminando en el que contiene `hasta`, en orden."""
    ancla = datetime(hasta.year, hasta.month, hasta.day)
    claves = []
    for i in range(cantidad):
        if granularidad == "dia":
            claves.append(clave_de(ancla - timedelta(days=i), "dia"))
        elif granularidad == "semana":
            claves.append(clave_de(ancla - timedelta(weeks=i), "semana"))
        elif granularidad == "mes":
            anio, mes = hasta.year, hasta.month - i
            while mes <= 0:
                anio, mes = anio - 1, mes + 12
            claves.append(f"{anio:04d}-{mes:02d}")
        elif granularidad == "anio":
            claves.append(f"{hasta.year - i:04d}")
        else:
            raise ValueError("granularidad desconocida")
    return claves[::-1]


def nombre_de_usuario(id_carpeta: str, correos: dict[str, str]) -> str:
    return correos.get(id_carpeta) or f"{SIN_USUARIO} {id_carpeta[:6]}"


def serie(usos: dict[str, list[float]], correos: dict[str, str], granularidad: str, claves: list[str]) -> list[dict]:
    """Filas largas {periodo, etiqueta, usuario, escaneos} para un gráfico apilado por usuario, solo con los
    períodos de `claves` (los que quedan fuera de la ventana no se cuentan)."""
    validas = set(claves)
    cuenta = defaultdict(int)
    for id_carpeta, horas in usos.items():
        quien = nombre_de_usuario(id_carpeta, correos)
        for t in horas:
            clave = clave_de(datetime.fromtimestamp(t), granularidad)
            if clave in validas:
                cuenta[(clave, quien)] += 1
    return [{"periodo": c, "etiqueta": etiqueta_de(c, granularidad), "usuario": u, "escaneos": n}
            for (c, u), n in sorted(cuenta.items())]


def totales(usos: dict[str, list[float]], hoy: date) -> dict:
    """Los números grandes: escaneos de hoy, de esta semana, de este mes, de este año y en total, y cuándo fue el último."""
    ahora = datetime(hoy.year, hoy.month, hoy.day)
    claves_actuales = {g: clave_de(ahora, g) for g in GRANULARIDADES}
    resultado = {"hoy": 0, "semana": 0, "mes": 0, "anio": 0, "total": 0, "ultimo": None}
    for horas in usos.values():
        for t in horas:
            momento = datetime.fromtimestamp(t)
            resultado["total"] += 1
            for g, nombre in (("dia", "hoy"), ("semana", "semana"), ("mes", "mes"), ("anio", "anio")):
                if clave_de(momento, g) == claves_actuales[g]:
                    resultado[nombre] += 1
            if resultado["ultimo"] is None or t > resultado["ultimo"]:
                resultado["ultimo"] = t
    return resultado


def tabla_por_usuario(usos: dict[str, list[float]], correos: dict[str, str], hoy: date) -> list[dict]:
    """Un renglón por usuario: total, este mes y último escaneo; el que más escanea primero."""
    clave_mes = clave_de(datetime(hoy.year, hoy.month, hoy.day), "mes")
    filas = []
    for id_carpeta, horas in usos.items():
        if not horas:
            continue
        filas.append({
            "usuario": nombre_de_usuario(id_carpeta, correos),
            "vigente": id_carpeta in correos,
            "total": len(horas),
            "este_mes": sum(1 for t in horas if clave_de(datetime.fromtimestamp(t), "mes") == clave_mes),
            "ultimo": max(horas),
        })
    return sorted(filas, key=lambda f: (-f["total"], f["usuario"]))
