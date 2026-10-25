"""
Gasto de Google Cloud por servicio (lógica pura de la pestaña "Gastos" de administracion/admin.py)
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

# Los servicios que de verdad aparecen en la factura de esta herramienta (el job y el bucket, y la VM y
# su red), en un orden fijo: su posición es su color. Todo lo demás se pliega en "Otros".
SERVICIOS_CONOCIDOS = [
    "Cloud Run",
    "Compute Engine",
    "Networking",
    "VM Manager",
    "Cloud Storage",
    "Secret Manager",
    "Artifact Registry",
]
OTROS = "Otros"

# Líneas de la factura que no son consumo de un servicio (ajustes de redondeo, etc.): no se grafican,
# porque aparecen en negativo y dibujarían una barra bajo cero. Siguen en los totales y en la tabla.
AJUSTES_DE_FACTURA = {"Invoice"}

# Los siete tonos validados (scripts/validate_palette.js, modos claro y oscuro), mismo orden en ambos.
PALETA_CLARA = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]
PALETA_OSCURA = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"]
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
        if f["servicio"] in AJUSTES_DE_FACTURA:
            continue
        acumulado[(str(f["dia"])[:10], nombre_de_serie(f["servicio"]))] += monto(f, medida)
    return [
        {"dia": d, "periodo": d, "etiqueta": etiqueta_de_dia(d), "servicio": s, "orden": orden_de_serie(s), "monto": round(v, 6)}
        for (d, s), v in sorted(acumulado.items())
    ]


def etiquetas_en_orden(datos: list[dict]) -> list[str]:
    """Etiquetas de día únicas, en orden cronológico (para el eje del gráfico)."""
    vistas = {}
    for d in sorted(datos, key=lambda x: x["dia"]):
        vistas.setdefault(d["etiqueta"], None)
    return list(vistas)


def dias_del_periodo(dias: int, hasta) -> list[str]:
    """Todos los días ISO de los últimos `dias` días terminando en `hasta` (un date), para que el eje
    muestre también los días sin datos en vez de juntar los que sí tienen."""
    from datetime import timedelta

    return [(hasta - timedelta(days=i)).isoformat() for i in range(dias - 1, -1, -1)]


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


# --------------------------------------------------------------------------- vista "últimos días"

def _rango_de_dias(dias: int, hasta, retroceso: int = 0) -> tuple[str, str]:
    """(primer día, último día) ISO de una ventana de `dias` días que termina en `hasta` menos `retroceso` días."""
    from datetime import timedelta

    fin = hasta - timedelta(days=retroceso)
    return (fin - timedelta(days=dias - 1)).isoformat(), fin.isoformat()


def recortar_dias(filas: list[dict], dias: int, hasta, retroceso: int = 0) -> list[dict]:
    """Solo las filas de los últimos `dias` días terminando en `hasta` (un date); con `retroceso`, la
    misma ventana corrida esos días hacia atrás (retroceso=dias da el período anterior)."""
    desde, fin = _rango_de_dias(dias, hasta, retroceso)
    return [f for f in filas if desde <= str(f["dia"])[:10] <= fin]


def comparar_periodos(filas: list[dict], dias: int, hasta) -> dict:
    """Totales de los últimos `dias` días contra los `dias` anteriores (misma duración), para ver
    si el gasto crece o baja. `anterior_tiene_datos` evita comparar contra un período vacío."""
    desde_a, hasta_a = _rango_de_dias(dias, hasta)
    desde_p, hasta_p = _rango_de_dias(dias, hasta, retroceso=dias)
    actual = [f for f in filas if desde_a <= str(f["dia"])[:10] <= hasta_a]
    previo = [f for f in filas if desde_p <= str(f["dia"])[:10] <= hasta_p]
    return {
        "actual": totales(actual), "anterior": totales(previo),
        "actual_tiene_datos": bool(actual), "anterior_tiene_datos": bool(previo),
        "anterior_desde": desde_p, "anterior_hasta": hasta_p,
    }


def variacion(actual: float, anterior: float) -> float | None:
    """Cambio relativo (0.25 = +25 %), o None si no hay base para comparar."""
    if not anterior:
        return None
    return (actual - anterior) / abs(anterior)


def texto_variacion(actual: float, anterior: float, moneda: str = "USD") -> str | None:
    """"+0.0100 USD (+12 %)" para el delta de una tarjeta; None si no hay base para comparar."""
    v = variacion(actual, anterior)
    if v is None:
        return None
    diferencia = actual - anterior
    signo = "+" if diferencia >= 0 else "-"
    return f"{signo}{abs(diferencia):,.4f} {moneda} ({v * 100:+.0f} %)"


# --------------------------------------------------------------------------- vista "por mes"

def mes_de(dia: str) -> str:
    return str(dia)[:7]


def etiqueta_de_mes(mes: str) -> str:
    """"2026-09" -> "sep 2026" (texto ya formateado, por la misma razón que las etiquetas de día)."""
    anio, m = mes.split("-")
    return f"{MESES[int(m) - 1]} {anio}"


def meses_del_periodo(cantidad: int, hasta) -> list[str]:
    """Los últimos `cantidad` meses ("YYYY-MM", en orden) terminando en el mes de `hasta`."""
    anio, mes = hasta.year, hasta.month
    meses = []
    for _ in range(cantidad):
        meses.append(f"{anio:04d}-{mes:02d}")
        mes -= 1
        if mes == 0:
            anio, mes = anio - 1, 12
    return meses[::-1]


def mes_anterior(mes: str) -> str:
    anio, m = int(mes[:4]), int(mes[5:7])
    return f"{anio - 1:04d}-12" if m == 1 else f"{anio:04d}-{m - 1:02d}"


def serie_mensual(filas: list[dict], medida: str) -> list[dict]:
    """Igual que serie_diaria pero sumando por mes: {periodo, etiqueta, servicio, orden, monto}."""
    acumulado = defaultdict(float)
    for f in filas:
        if f["servicio"] in AJUSTES_DE_FACTURA:
            continue
        acumulado[(mes_de(f["dia"]), nombre_de_serie(f["servicio"]))] += monto(f, medida)
    return [
        {"periodo": m, "etiqueta": etiqueta_de_mes(m), "servicio": s, "orden": orden_de_serie(s), "monto": round(v, 6)}
        for (m, s), v in sorted(acumulado.items())
    ]


# Un mes cuenta como "completo" para comparar si hay datos de al menos esta fracción de sus días. Comparar
# totales de un mes con 2 días de datos contra otro con 30 daría "+1300 %": un número sin sentido.
COBERTURA_MINIMA = 0.9


def dias_del_mes(mes: str) -> int:
    from calendar import monthrange

    return monthrange(int(mes[:4]), int(mes[5:7]))[1]


def resumen_mensual(filas: list[dict]) -> list[dict]:
    """Un renglón por mes con datos: totales (bruto, créditos, neto), cuántos de sus días tienen datos y el
    bruto promedio por día con datos. Las variaciones contra el mes calendario anterior solo se calculan
    cuando se pueden comparar de verdad:
      - variacion_bruto / variacion_neto (totales): ambos meses con cobertura >= COBERTURA_MINIMA.
      - variacion_promedio (bruto por día): el mes anterior con cobertura suficiente; sirve aunque el mes
        actual vaya a medias, porque promedia solo los días que tienen datos."""
    por_mes = defaultdict(list)
    for f in filas:
        por_mes[mes_de(f["dia"])].append(f)
    base = {}
    for m, fs in por_mes.items():
        dias_con_datos = len({str(f["dia"])[:10] for f in fs})
        t = totales(fs)
        base[m] = {
            "mes": m, **t, "dias_con_datos": dias_con_datos, "dias_del_mes": dias_del_mes(m),
            "cobertura": dias_con_datos / dias_del_mes(m),
            "promedio_diario_bruto": t["bruto"] / dias_con_datos,
        }
    filas_resumen = []
    for m in sorted(base):
        actual, previo = base[m], base.get(mes_anterior(m))
        previo_ok = bool(previo) and previo["cobertura"] >= COBERTURA_MINIMA
        ambos_ok = previo_ok and actual["cobertura"] >= COBERTURA_MINIMA
        filas_resumen.append({
            **actual,
            "variacion_bruto": variacion(actual["bruto"], previo["bruto"]) if ambos_ok else None,
            "variacion_neto": variacion(actual["neto"], previo["neto"]) if ambos_ok else None,
            "variacion_promedio": variacion(actual["promedio_diario_bruto"], previo["promedio_diario_bruto"]) if previo_ok else None,
        })
    return filas_resumen


def es_parcial(mes: str, ultimo_dia: str, hoy) -> bool:
    """Un mes está incompleto si es el mes en curso, o si los datos se cortan antes de su último día."""
    from calendar import monthrange

    if mes == f"{hoy.year:04d}-{hoy.month:02d}":
        return True
    if mes_de(ultimo_dia) != mes:
        return False
    anio, m = int(mes[:4]), int(mes[5:7])
    return str(ultimo_dia)[:10] < f"{mes}-{monthrange(anio, m)[1]:02d}"


# --------------------------------------------------------------------------- detalle por concepto

def es_egreso(sku: str) -> bool:
    """¿Es una salida de datos (egreso / transferencia hacia afuera)? Por el nombre del SKU."""
    s = (sku or "").lower()
    return "egress" in s or "transfer out" in s


def en_europa(ubicacion: str) -> bool:
    u = (ubicacion or "").lower()
    return u.startswith("europe") or u == "eu"


def _clave_concepto(f: dict) -> tuple[str, str, str]:
    return f["servicio"], f.get("sku") or "(sin SKU)", f.get("ubicacion") or "-"


def tabla_por_concepto(filas: list[dict]) -> list[dict]:
    """Detalle por servicio + SKU + región (el nivel en que la factura explica de dónde sale el gasto),
    de mayor a menor valor bruto, con marcas de egreso de datos y de región europea."""
    acumulado = defaultdict(lambda: {"bruto": 0.0, "creditos": 0.0})
    for f in filas:
        a = acumulado[_clave_concepto(f)]
        a["bruto"] += float(f.get("bruto") or 0)
        a["creditos"] += float(f.get("creditos") or 0)
    filas_tabla = [
        {"grupo": "(ajuste, no se grafica)" if s in AJUSTES_DE_FACTURA else nombre_de_serie(s),
         "servicio": s, "sku": k, "ubicacion": u, "bruto": v["bruto"], "creditos": v["creditos"],
         "neto": v["bruto"] + v["creditos"], "egreso": es_egreso(k), "europa": en_europa(u)}
        for (s, k, u), v in acumulado.items()
    ]
    return sorted(filas_tabla, key=lambda r: (-abs(r["bruto"]), r["servicio"], r["sku"]))


def _bruto_por_concepto(filas: list[dict], por_dia: bool) -> dict:
    acumulado = defaultdict(float)
    for f in filas:
        acumulado[_clave_concepto(f)] += float(f.get("bruto") or 0)
    if por_dia:
        dias = len({str(f["dia"])[:10] for f in filas}) or 1
        acumulado = {k: v / dias for k, v in acumulado.items()}
    return dict(acumulado)


def cambios_por_concepto(filas_ahora: list[dict], filas_antes: list[dict], por_dia: bool = False) -> list[dict]:
    """Qué conceptos explican la diferencia entre dos conjuntos de filas (por ejemplo, dos meses), del que
    más subió al que más bajó. por_dia=True compara el promedio por día con datos de cada lado, que sirve
    cuando uno de los dos está incompleto. Solo el valor bruto: el neto lo tapan los créditos."""
    ahora = _bruto_por_concepto(filas_ahora, por_dia)
    antes = _bruto_por_concepto(filas_antes, por_dia)
    cambios = []
    for clave in set(ahora) | set(antes):
        a, b = ahora.get(clave, 0.0), antes.get(clave, 0.0)
        if abs(a - b) < 1e-9:
            continue
        servicio, sku, ubicacion = clave
        cambios.append({"servicio": servicio, "sku": sku, "ubicacion": ubicacion, "ahora": a, "antes": b,
                        "diferencia": a - b, "egreso": es_egreso(sku), "europa": en_europa(ubicacion)})
    return sorted(cambios, key=lambda r: (-r["diferencia"], r["servicio"], r["sku"]))
