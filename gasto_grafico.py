"""
Gráfico de gasto diario apilado por servicio (Altair)
-----------------------------------------------------
Separado de admin.py para poder dibujarlo con datos de ejemplo y revisarlo.
Reglas del skill de visualización: barras apiladas (parte del total por día),
colores categóricos fijos por servicio (ver gasto.py), 2px de fondo entre
segmentos, grilla y ejes recesivos, leyenda siempre presente, tooltip por
segmento, y una vista de tabla aparte en admin.py.
"""

import altair as alt

import gasto

SUPERFICIE = {"claro": "#fcfcfb", "oscuro": "#1a1a19"}
TINTA_SECUNDARIA = {"claro": "#52514e", "oscuro": "#c3c2b7"}
REJILLA = {"claro": "#e1e0d9", "oscuro": "#2c2c2a"}
TINTA_APAGADA = "#898781"  # etiquetas de ejes, igual en ambos modos


def construir(datos: list[dict], moneda: str, oscuro: bool) -> alt.Chart:
    modo = "oscuro" if oscuro else "claro"
    series = gasto.series_presentes(datos)
    return (
        alt.Chart(alt.Data(values=datos))
        .mark_bar(stroke=SUPERFICIE[modo], strokeWidth=2)  # 2px de fondo entre segmentos
        .encode(
            # Un día = una barra con ancho propio (un eje temporal continuo las deja finísimas),
            # y la etiqueta ya viene como texto (ver gasto.etiqueta_de_dia: evita el corrimiento de zona horaria).
            x=alt.X(
                "etiqueta:O", title=None, sort=gasto.etiquetas_en_orden(datos),
                axis=alt.Axis(labelAngle=0, labelOverlap="parity", labelColor=TINTA_APAGADA,
                              domainColor=REJILLA[modo], tickColor=REJILLA[modo], grid=False),
            ),
            order=alt.Order("orden:Q", sort="ascending"),
            y=alt.Y(
                "monto:Q", title=f"{moneda} por día",
                axis=alt.Axis(labelColor=TINTA_APAGADA, titleColor=TINTA_SECUNDARIA[modo], gridColor=REJILLA[modo], domain=False, tickColor=REJILLA[modo]),
            ),
            color=alt.Color(
                "servicio:N", title=None,
                scale=alt.Scale(domain=series, range=[gasto.color_de(s, oscuro) for s in series]),
                legend=alt.Legend(orient="bottom", labelColor=TINTA_SECUNDARIA[modo], symbolType="square"),
            ),
            tooltip=[
                alt.Tooltip("dia:N", title="Día"),
                alt.Tooltip("servicio:N", title="Servicio"),
                alt.Tooltip("monto:Q", title=moneda, format=".4f"),
            ],
        )
        .properties(height=320, background="transparent")
        .configure_view(stroke=None)
    )
