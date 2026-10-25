"""
Gasto de Google Cloud por servicio (lógica pura de la pestaña "Gastos" de admin.py)
----------------------------------------------------------------------------------
Toma las filas de la exportación de facturación a BigQuery (ya agregadas por
día / proyecto / servicio) y las deja listas para graficar: totales, una serie
diaria apilada con colores fijos por servicio y una tabla por servicio.

Sin dependencias de la nube ni de Streamlit, para poder probarlo.

Colores: paleta categórica validada (scripts/validate_palette.js del skill de
visualización, modos claro y oscuro: pasa CVD, banda de luminosidad y
separación; en claro tres tonos quedan bajo 3:1 de contraste con el fondo, y lo
compensan la leyenda, los tooltips, la separación entre segmentos y la vista de
tabla). El color sigue a la entidad: un servicio conocido conserva SIEMPRE su
color, aunque otro servicio aparezca o desaparezca; todo lo demás se pliega en
"Otros" (gris).
"""

from collections import defaultdict

# Servicios que usa esta herramienta, en un orden fijo: su posición es su color.
SERVICIOS_CONOCIDOS = [
    "Cloud Run",
    "Cloud Storage",
    "Compute Engine",
    "Secret Manager",
    "Artifact Registry",
    "Cloud Build",
]
OTROS = "Otros"

# Primeros seis de los siete tonos validados, mismo orden en ambos modos.
PALETA_CLARA = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
PALETA_OSCURA = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"]
GRIS_OTROS = "#898781"  # tono "muted" de la paleta, igual en ambos modos

MEDIDAS = ("bruto", "neto")

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def monto(fila: dict, medida: str) -> float:
    """bruto = valor de lo usado antes de descuentos; neto = lo que realmente
    se paga (bruto más créditos, que vienen en negativo: free tier, etc.)."""
    if medida not in MEDIDAS:
        raise ValueError("medida desconocida")
    bruto = float(fila.get("bruto") or 0)
    return bruto if medida == "bruto" else bruto + float(fila.get("creditos") or 0)


def moneda_principal(filas: list[dict]) -> str:
    cuenta = defaultdict(int)
    for f in filas:
        cuenta[f.get("moneda") or "USD"] += 1
    return max(cuenta, key=cuenta.get) if cuenta else "USD"


def solo_moneda(filas: list[dict], moneda: str) -> list[dict]:
    """Sumar monedas distintas sería un número sin sentido: se queda con una."""
    return [f for f in filas if (f.get("moneda") or "USD") == moneda]


def totales(filas: list[dict]) -> dict:
    bruto = sum(float(f.get("bruto") or 0) for f in filas)
    creditos = sum(float(f.get("creditos") or 0) for f in filas)
    return {"bruto": bruto, "creditos": creditos, "neto": bruto + creditos}


def nombre_de_serie(servicio: str) -> str:
    return servicio if servicio in SERVICIOS_CONOCIDOS else OTROS


def color_de(serie: str, oscuro: bool) -> str:
    if serie == OTROS:
        return GRIS_OTROS
    paleta = PALETA_OSCURA if oscuro else PALETA_CLARA
    return paleta[SERVICIOS_CONOCIDOS.index(serie)]


def etiqueta_de_dia(dia: str) -> str:
    """"2026-10-01" -> "01 oct". Texto ya formateado a propósito: pasarle la
    fecha al gráfico como fecha la interpreta en UTC y la muestra en hora
    local, así que en Chile cada día aparecería corrido uno hacia atrás."""
    anio, mes, d = str(dia)[:10].split("-")
    return f"{int(d):02d} {MESES[int(mes) - 1]}"


def orden_de_serie(serie: str) -> int:
    return SERVICIOS_CONOCIDOS.index(serie) if serie in SERVICIOS_CONOCIDOS else len(SERVICIOS_CONOCIDOS)


def serie_diaria(filas: list[dict], medida: str) -> list[dict]:
    """Filas largas {dia, etiqueta, servicio, orden, monto} para un gráfico
    apilado: servicios conocidos por separado y el resto sumado en "Otros".
    Días ordenados; `orden` fija el apilado (el primero abajo, como la leyenda)."""
    acumulado = defaultdict(float)
    for f in filas:
        acumulado[(str(f["dia"])[:10], nombre_de_serie(f["servicio"]))] += monto(f, medida)
    return [
        {"dia": d, "etiqueta": etiqueta_de_dia(d), "servicio": s, "orden": orden_de_serie(s), "monto": round(v, 6)}
        for (d, s), v in sorted(acumulado.items())
    ]


def etiquetas_en_orden(datos: list[dict]) -> list[str]:
    """Etiquetas de día únicas, en orden cronológico (para el eje del gráfico)."""
    vistas = {}
    for d in sorted(datos, key=lambda x: x["dia"]):
        vistas.setdefault(d["etiqueta"], None)
    return list(vistas)


def series_presentes(datos: list[dict]) -> list[str]:
    """Series que aparecen, en el orden fijo de colores (y "Otros" al final)."""
    vistas = {d["servicio"] for d in datos}
    return [s for s in SERVICIOS_CONOCIDOS if s in vistas] + ([OTROS] if OTROS in vistas else [])


def tabla_por_servicio(filas: list[dict]) -> list[dict]:
    """Detalle completo (sin plegar en "Otros"), de mayor a menor valor bruto."""
    por_servicio = defaultdict(lambda: {"bruto": 0.0, "creditos": 0.0})
    for f in filas:
        acc = por_servicio[f["servicio"]]
        acc["bruto"] += float(f.get("bruto") or 0)
        acc["creditos"] += float(f.get("creditos") or 0)
    filas_tabla = [
        {"servicio": s, "bruto": v["bruto"], "creditos": v["creditos"], "neto": v["bruto"] + v["creditos"]}
        for s, v in por_servicio.items()
    ]
    return sorted(filas_tabla, key=lambda r: (-r["bruto"], r["servicio"]))


def proyectos(filas: list[dict]) -> list[str]:
    return sorted({f.get("proyecto") or "(sin proyecto)" for f in filas})


def filtrar_proyecto(filas: list[dict], proyecto: str | None) -> list[dict]:
    if not proyecto:
        return filas
    return [f for f in filas if (f.get("proyecto") or "(sin proyecto)") == proyecto]


def formatear(valor: float, moneda: str = "USD") -> str:
    """Montos chicos (centavos) con 4 decimales para que no se vean como 0.00."""
    signo = "-" if valor < 0 else ""
    return f"{signo}{abs(valor):,.4f} {moneda}"
