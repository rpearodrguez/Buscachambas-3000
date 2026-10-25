"""
Gráfico de escaneos por período, apilado por usuario (Altair)
-------------------------------------------------------------
Mismo estilo que el de gastos (barras con 2px de fondo entre segmentos, ejes recesivos, leyenda siempre
presente, tooltip por segmento) y la misma paleta categórica validada. El color sigue a la persona: cada
usuario conserva el suyo (por orden alfabético de su correo); pasados siete, el resto se pliega en "Otros".
"""

import altair as alt

from administracion import gasto
from administracion.gasto_grafico import REJILLA, SUPERFICIE, TINTA_APAGADA, TINTA_SECUNDARIA

OTROS = "Otros"
MAX_SERIES = len(gasto.PALETA_CLARA)


def agrupar_usuarios(datos: list[dict]) -> list[dict]:
    """Los primeros siete usuarios (orden alfabético, estable) conservan su serie; el resto se suma en "Otros"."""
    usuarios = sorted({d["usuario"] for d in datos})
    propios = set(usuarios[:MAX_SERIES]) if len(usuarios) <= MAX_SERIES else set(usuarios[:MAX_SERIES - 1])
    acumulado = {}
    for d in datos:
        nombre = d["usuario"] if d["usuario"] in propios else OTROS
        clave = (d["periodo"], nombre)
        base = acumulado.setdefault(clave, {"periodo": d["periodo"], "etiqueta": d["etiqueta"], "usuario": nombre, "escaneos": 0})
        base["escaneos"] += d["escaneos"]
    return sorted(acumulado.values(), key=lambda f: (f["periodo"], f["usuario"]))


def colores_por_usuario(datos: list[dict], oscuro: bool) -> dict[str, str]:
    paleta = gasto.PALETA_OSCURA if oscuro else gasto.PALETA_CLARA
    nombres = sorted({d["usuario"] for d in datos if d["usuario"] != OTROS})
    colores = {nombre: paleta[i] for i, nombre in enumerate(nombres)}
    if any(d["usuario"] == OTROS for d in datos):
        colores[OTROS] = gasto.GRIS_OTROS
    return colores


def construir(datos: list[dict], etiquetas_eje: list[str], oscuro: bool, unidad: str) -> alt.Chart:
    """datos: filas {periodo, etiqueta, usuario, escaneos}, ya agrupadas con agrupar_usuarios()."""
    modo = "oscuro" if oscuro else "claro"
    colores = colores_por_usuario(datos, oscuro)
    series = list(colores)
    return (
        alt.Chart(alt.Data(values=datos))
        .mark_bar(stroke=SUPERFICIE[modo], strokeWidth=2)
        .encode(
            x=alt.X(
                "etiqueta:O", title=None, sort=etiquetas_eje, scale=alt.Scale(domain=etiquetas_eje),
                axis=alt.Axis(labelAngle=0, labelOverlap="parity", labelColor=TINTA_APAGADA,
                              domainColor=REJILLA[modo], tickColor=REJILLA[modo], grid=False),
            ),
            y=alt.Y(
                "escaneos:Q", title=f"Escaneos por {unidad}",
                axis=alt.Axis(labelColor=TINTA_APAGADA, titleColor=TINTA_SECUNDARIA[modo], gridColor=REJILLA[modo],
                              domain=False, tickColor=REJILLA[modo], tickMinStep=1, format="d"),
            ),
            color=alt.Color(
                "usuario:N", title=None,
                scale=alt.Scale(domain=series, range=[colores[s] for s in series]),
                legend=alt.Legend(orient="bottom", labelColor=TINTA_SECUNDARIA[modo], symbolType="square"),
            ),
            tooltip=[alt.Tooltip("periodo:N", title=unidad.capitalize()), alt.Tooltip("usuario:N", title="Usuario"),
                     alt.Tooltip("escaneos:Q", title="Escaneos")],
        )
        .properties(height=300, background="transparent")
        .configure_view(stroke=None)
    )
