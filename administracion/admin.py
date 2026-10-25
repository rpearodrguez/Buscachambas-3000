"""
Administración del Job Scanner (SOLO en tu PC)
----------------------------------------------
Herramienta local para no andar escribiendo comandos en Cloud Shell cada vez
que hay que cambiar algo: conexión con tu proyecto, correo SMTP (servidor,
usuario, token) con correo de prueba, y la lista de usuarios autorizados.

Corre con TUS credenciales de Google (no las de la GUI pública): la GUI
pública no tiene ningún permiso para cambiar esta configuración.

    pip install -r requirements-admin.txt
    gcloud auth application-default login      # una vez, entras con tu cuenta de Google
    streamlit run administracion/admin.py --server.address localhost

Los datos de conexión (proyecto, región, job, bucket) se guardan en
admin_local.json, que está en .gitignore: el repo es público y nada de eso
va ahí. El token SMTP se escribe sin mostrarse, va directo a Secret Manager y
no se puede volver a leer desde aquí.
"""

import json
import os
import sys
import time
from datetime import date, datetime, timezone

import streamlit as st

# Los puntos de entrada viven en subcarpetas: la raíz del repo va en sys.path
# para poder importar los paquetes hermanos (comun, escaner, ...).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)  # admin_local.json y demás archivos locales viven en la raíz del repo

from administracion import admin_nube, gasto, gasto_grafico  # noqa: E402
from comun import limpieza, usuarios  # noqa: E402
from comun import almacen as almacenamiento  # noqa: E402
from escaner import scan_getonbrd as scanner  # noqa: E402  (solo para la lista de países)

st.set_page_config(page_title="Administración — Job Scanner", layout="wide")
st.title("Administración")
st.caption("Solo para el administrador, corre en tu PC con tus credenciales de Google. No se publica.")


def _mostrar_error(e: Exception, contexto: str):
    st.error(f"{contexto}: {e}")


cfg = admin_nube.cargar_config_local()

tab_conexion, tab_correo, tab_usuarios, tab_gastos, tab_limpieza = st.tabs(
    ["Conexión", "Correo (SMTP)", "Usuarios", "Gastos", "Limpieza"]
)

# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------
with tab_conexion:
    st.subheader("Conexión con tu proyecto")
    st.caption(f"Se guarda en {admin_nube.CONFIG_LOCAL} (solo en este equipo, fuera del repositorio).")
    actual = cfg or {}
    with st.form("form_conexion"):
        project = st.text_input("ID del proyecto", value=actual.get("project", ""))
        region = st.text_input("Región del job", value=actual.get("region", ""), placeholder="la región donde creaste el job")
        job = st.text_input("Nombre del job", value=actual.get("job", ""))
        bucket = st.text_input("Bucket (sin gs://)", value=actual.get("bucket", ""))
        secret = st.text_input("Secreto del token SMTP", value=actual.get("secret", ""))
        billing_tabla = st.text_input(
            "Tabla de facturación (opcional, para la pestaña Gastos)", value=actual.get("billing_tabla", ""),
            placeholder="proyecto.dataset.gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX",
        )
        if st.form_submit_button("Guardar"):
            try:
                admin_nube.guardar_config_local(
                    {"project": project, "region": region, "job": job, "bucket": bucket, "secret": secret,
                     "billing_tabla": billing_tabla}
                )
                st.rerun()
            except admin_nube.ErrorAdmin as e:
                st.error(str(e))

    if cfg:
        if st.button("Probar conexión"):
            try:
                st.cache_resource.clear()
                almacenamiento.crear_almacen(admin_nube.destino_admin(cfg), credenciales=admin_nube.credenciales_admin(cfg))
                st.success("Bucket accesible y privado.")
                actual_smtp = admin_nube.leer_smtp_del_job(cfg)
                st.success(f"Job encontrado (servidor SMTP actual: {actual_smtp['host'] or 'sin configurar'}).")
            except Exception as e:  # es la PC del administrador: se muestra el detalle
                _mostrar_error(e, "Falló la conexión")
    else:
        st.info("Completa y guarda la conexión para usar las otras pestañas.")


@st.cache_resource
def _almacen_admin(destino: str, cfg_json: str):
    # cfg_json solo entra para que la caché distinga conexiones distintas
    import json
    return almacenamiento.crear_almacen(destino, credenciales=admin_nube.credenciales_admin(json.loads(cfg_json)))


# ---------------------------------------------------------------------------
# Correo (SMTP)
# ---------------------------------------------------------------------------
with tab_correo:
    if not cfg:
        st.info("Primero configura la conexión.")
    else:
        st.subheader("Correo que envía los avisos")
        st.caption(
            "Servidor, puerto y usuario se guardan en el job. El token se agrega como versión nueva "
            "en Secret Manager (se conservan la nueva y la anterior) y no se vuelve a mostrar."
        )
        try:
            if "smtp_actual" not in st.session_state:
                st.session_state["smtp_actual"] = admin_nube.leer_smtp_del_job(cfg)
        except Exception as e:
            _mostrar_error(e, "No se pudo leer la configuración del job")
            st.session_state["smtp_actual"] = {"host": "", "port": "587", "usuario": ""}
        smtp = st.session_state["smtp_actual"]

        with st.form("form_smtp"):
            host = st.text_input("Servidor SMTP", value=smtp["host"], placeholder="smtp.protonmail.ch")
            puerto = st.number_input("Puerto", value=int(smtp["port"] or 587), min_value=1, max_value=65535, step=1)
            usuario = st.text_input("Usuario (dirección que envía)", value=smtp["usuario"])
            token = st.text_input("Token / contraseña SMTP", type="password", placeholder="vacío = no cambiarlo")
            destino_prueba = st.text_input("Correo de prueba (a dónde mandar la prueba)")
            col_g, col_p = st.columns(2)
            guardar = col_g.form_submit_button("Guardar")
            probar = col_p.form_submit_button("Guardar y probar")

        if guardar or probar:
            try:
                h, p, u = admin_nube.validar_smtp(host, puerto, usuario)
                destino = usuarios.validar_correo(destino_prueba) if probar else None
                with st.spinner("Guardando en el job..."):
                    admin_nube.actualizar_smtp_job(cfg, h, p, u)
                if token.strip():
                    with st.spinner("Guardando el token en Secret Manager..."):
                        destruidas = admin_nube.reemplazar_token(cfg, token)
                    st.success(f"Token actualizado ({destruidas} versión(es) vieja(s) eliminada(s)).")
                st.session_state["smtp_actual"] = {"host": h, "port": str(p), "usuario": u}
                st.success("Servidor, puerto y usuario guardados en el job.")
                if probar:
                    with st.spinner("Ejecutando el job para mandar el correo de prueba (puede tardar un poco)..."):
                        ok, mensaje = admin_nube.probar_correo(cfg, destino)
                    (st.success if ok else st.error)(mensaje)
            except (admin_nube.ErrorAdmin, usuarios.ErrorUsuarios) as e:
                st.error(str(e))
            except Exception as e:
                _mostrar_error(e, "No se pudo completar la operación")

# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
NUEVO_USUARIO = "(nuevo usuario)"
SIN_PAIS = "(el primero de la lista)"


def _pestana_usuarios(cfg):
    st.subheader("Usuarios autorizados")
    try:
        almacen_admin = _almacen_admin(admin_nube.destino_admin(cfg), json.dumps(cfg, sort_keys=True))
        datos = usuarios.cargar(almacen_admin)
    except Exception as e:
        _mostrar_error(e, "No se pudo leer la lista de usuarios")
        return  # solo esta pestaña falla; las demás siguen

    paises = list(scanner.PAISES)
    if datos["usuarios"]:
        st.dataframe(
            [{"correo": c, "rol": u.get("rol", "usuario"),
              "cuota semanal": u.get("cuota_semanal", usuarios.CUOTA_SEMANAL_POR_DEFECTO),
              "país por defecto": u.get("pais_por_defecto") or "—"}
             for c, u in sorted(datos["usuarios"].items())],
            width="stretch", hide_index=True,
        )
    else:
        st.warning("Todavía no hay usuarios. Agrega primero tu propio correo como administrador.")

    st.markdown("**Agregar o editar un usuario**")
    elegido = st.selectbox(
        "Usuario", [NUEVO_USUARIO] + sorted(datos["usuarios"]),
        help="Elige uno existente para cambiar su rol, cuota o país sin perder el resto de su ficha.",
    )
    ficha = datos["usuarios"].get(elegido, {})
    # La clave del formulario incluye al usuario: al elegir a otro, los campos se rellenan con su ficha.
    with st.form(f"form_usuario_{elegido}"):
        if elegido == NUEVO_USUARIO:
            correo = st.text_input("Correo (el que usa para entrar con Google)")
        else:
            correo = elegido
            st.text_input("Correo", value=elegido, disabled=True)
        col_r, col_c, col_p = st.columns(3)
        rol_actual = ficha.get("rol")
        rol = col_r.selectbox("Rol", usuarios.ROLES, index=usuarios.ROLES.index(rol_actual) if rol_actual in usuarios.ROLES else 0)
        cuota = col_c.number_input(
            "Scans por semana", value=int(ficha.get("cuota_semanal", usuarios.CUOTA_SEMANAL_POR_DEFECTO)),
            min_value=0, max_value=usuarios.CUOTA_MAXIMA, step=1,
        )
        opciones_pais = [SIN_PAIS] + paises
        pais_actual = usuarios.pais_por_defecto(ficha, paises)
        pais = col_p.selectbox(
            "País por defecto", opciones_pais, index=opciones_pais.index(pais_actual) if pais_actual else 0,
            help="Con este país parte la persona mientras no tenga elecciones guardadas; después elige el suyo y se le recuerda.",
        )
        if st.form_submit_button("Guardar usuario"):
            try:
                usuarios.guardar(almacen_admin, usuarios.agregar(datos, correo, rol, cuota, None if pais == SIN_PAIS else pais, paises))
                st.rerun()
            except usuarios.ErrorUsuarios as e:
                st.error(str(e))
            except Exception as e:
                _mostrar_error(e, "No se pudo guardar")

    if datos["usuarios"]:
        with st.form("form_quitar_usuario"):
            st.markdown("**Quitar un usuario**")
            quitar = st.selectbox("Usuario a quitar", sorted(datos["usuarios"]))
            if st.form_submit_button("Quitar"):
                try:
                    usuarios.guardar(almacen_admin, usuarios.quitar(datos, quitar))
                    st.rerun()
                except usuarios.ErrorUsuarios as e:
                    st.error(str(e))
                except Exception as e:
                    _mostrar_error(e, "No se pudo quitar")


with tab_usuarios:
    if not cfg:
        st.info("Primero configura la conexión.")
    else:
        _pestana_usuarios(cfg)

# ---------------------------------------------------------------------------
# Gastos
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def _gasto(cfg_tabla: str, proyecto: str):
    # Siempre el máximo (400 días): las dos vistas recortan lo que necesitan, y la tabla es chica.
    # cfg_tabla/proyecto entran como argumentos para que formen parte de la clave de la caché.
    return admin_nube.consultar_gasto({"billing_tabla": cfg_tabla, "project": proyecto}, 400)


def _tema_oscuro() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return True


def _tabla_por_servicio(filas, moneda, titulo):
    with st.expander(titulo):
        st.dataframe(
            [{"servicio": r["servicio"], f"bruto ({moneda})": round(r["bruto"], 4),
              f"créditos ({moneda})": round(r["creditos"], 4), f"neto ({moneda})": round(r["neto"], 4)}
             for r in gasto.tabla_por_servicio(filas)],
            width="stretch", hide_index=True,
        )


def _marcas(c):
    marcas = []
    if c["egreso"]:
        marcas.append("egreso de datos")
    if c["europa"]:
        marcas.append("región de Europa")
    return ", ".join(marcas)


def _detalle_conceptos(filas, moneda, titulo):
    """Cada SKU (concepto de la factura) con su región y el color del gráfico en que cae: aquí se ve qué hay
    dentro de "Otros" y de dónde sale cada costo."""
    with st.expander(titulo):
        st.dataframe(
            [{"grupo en el gráfico": c["grupo"], "servicio": c["servicio"], "concepto (SKU)": c["sku"],
              "región": c["ubicacion"], f"bruto ({moneda})": round(c["bruto"], 4), f"créditos ({moneda})": round(c["creditos"], 4),
              f"neto ({moneda})": round(c["neto"], 4), "marca": _marcas(c)}
             for c in gasto.tabla_por_concepto(filas)],
            width="stretch", hide_index=True,
        )
        st.caption(
            "Solo se listan los conceptos con costo o créditos distintos de cero. Los descuentos del free tier de Compute Engine "
            "aparecen como líneas negativas aparte («… free tier discount») que compensan el uso («E2 Instance …»)."
        )


def _tabla_cambios(filas_ahora, filas_antes, moneda, titulo, por_dia=False):
    """Qué conceptos explican que el gasto suba o baje entre dos períodos (del que más subió al que más bajó)."""
    cambios = gasto.cambios_por_concepto(filas_ahora, filas_antes, por_dia)
    unidad = "bruto por día" if por_dia else "bruto"
    with st.expander(titulo):
        if not cambios:
            st.info("No hay diferencias entre los dos períodos.")
            return
        st.dataframe(
            [{"concepto": f"{c['servicio']} · {c['sku']}", "región": c["ubicacion"],
              f"antes ({unidad}, {moneda})": round(c["antes"], 4), f"ahora ({unidad}, {moneda})": round(c["ahora"], 4),
              "diferencia": round(c["diferencia"], 4), "marca": _marcas(c)}
             for c in cambios],
            width="stretch", hide_index=True,
        )
        st.caption("Sube el que tiene diferencia positiva. Es el valor bruto: los créditos y descuentos pueden dejar el neto en cero.")


def _vista_dias(filas, medida, moneda, hasta, ultimo):
    """Los últimos días con su detalle diario, y cuánto sube o baja contra el período anterior de igual duración."""
    dias = st.selectbox("Período", [7, 14, 30], index=2, key="gastos_dias", format_func=lambda d: f"Últimos {d} días")
    c = gasto.comparar_periodos(filas, dias, hasta)
    if not c["actual_tiene_datos"]:
        st.warning(
            f"No hay datos en los últimos {dias} días. El dato más reciente de la exportación es del {ultimo}: "
            f"la exportación va por detrás y se pone al día sola en uno o dos días. "
            f"Mira la vista por mes para ver lo que ya llegó."
        )
        return
    a, b = c["actual"], c["anterior"]
    comparar = c["anterior_tiene_datos"]
    k1, k2, k3 = st.columns(3)
    # delta_color="inverse": en un gasto, que suba es lo malo (rojo) y que baje lo bueno (verde)
    k1.metric(f"Se paga (neto), {dias} días", gasto.formatear(a["neto"], moneda),
              delta=gasto.texto_variacion(a["neto"], b["neto"], moneda) if comparar else None, delta_color="inverse")
    k2.metric(f"Valor de lo usado (bruto), {dias} días", gasto.formatear(a["bruto"], moneda),
              delta=gasto.texto_variacion(a["bruto"], b["bruto"], moneda) if comparar else None, delta_color="inverse")
    k3.metric("Cubierto por créditos / free tier", gasto.formatear(-a["creditos"], moneda))
    st.caption(
        f"Variación contra los {dias} días anteriores ({c['anterior_desde']} a {c['anterior_hasta']})."
        if comparar else f"Sin datos en los {dias} días anteriores: no hay con qué comparar."
    )
    actual = gasto.recortar_dias(filas, dias, hasta)
    datos = gasto.serie_diaria(actual, medida)
    if any(d["monto"] for d in datos):
        etiquetas = [gasto.etiqueta_de_dia(d) for d in gasto.dias_del_periodo(dias, hasta)]
        st.altair_chart(gasto_grafico.construir(datos, moneda, _tema_oscuro(), etiquetas, "día"), width="stretch", theme=None)
    else:
        st.info("En este período no hay gasto en la medida elegida (los créditos lo cubren todo).")
    _tabla_por_servicio(actual, moneda, f"Ver tabla por servicio (últimos {dias} días)")
    _detalle_conceptos(actual, moneda, f"Ver detalle por concepto, SKU y región (últimos {dias} días)")
    previo = gasto.recortar_dias(filas, dias, hasta, retroceso=dias)
    if previo:
        _tabla_cambios(actual, previo, moneda, f"¿Qué explica el cambio contra los {dias} días anteriores?")


def _vista_meses(filas, medida, moneda, hasta, ultimo):
    """El gasto mes a mes, para ver la tendencia (crece o se reduce) en lugar del detalle de cada día."""
    cantidad = st.selectbox("Meses", [3, 6, 12], index=1, key="gastos_meses", format_func=lambda m: f"Últimos {m} meses")
    meses = gasto.meses_del_periodo(cantidad, hasta)
    en_rango = [f for f in filas if gasto.mes_de(f["dia"]) in meses]
    if not en_rango:
        st.warning(f"No hay datos en los últimos {cantidad} meses. El dato más reciente de la exportación es del {ultimo}.")
        return
    todos = {r["mes"]: r for r in gasto.resumen_mensual(filas)}
    resumen = [todos[m] for m in meses if m in todos]
    reciente = resumen[-1]
    parcial = gasto.es_parcial(reciente["mes"], ultimo, hasta)
    nombre = gasto.etiqueta_de_mes(reciente["mes"]) + (" (parcial)" if parcial else "")

    def delta(v):  # delta_color="inverse": en un gasto, subir es lo malo (rojo) y bajar lo bueno (verde)
        return None if v is None else f"{v * 100:+.0f} %"

    k1, k2, k3 = st.columns(3)
    k1.metric(f"Se paga (neto), {nombre}", gasto.formatear(reciente["neto"], moneda), delta=delta(reciente["variacion_neto"]), delta_color="inverse")
    k2.metric(f"Valor de lo usado (bruto), {nombre}", gasto.formatear(reciente["bruto"], moneda), delta=delta(reciente["variacion_bruto"]), delta_color="inverse")
    k3.metric("Bruto promedio por día", gasto.formatear(reciente["promedio_diario_bruto"], moneda), delta=delta(reciente["variacion_promedio"]), delta_color="inverse")
    notas = []
    if reciente["variacion_bruto"] is None:
        notas.append(
            f"Los totales no se comparan con el mes anterior: {nombre} tiene datos de {reciente['dias_con_datos']} de "
            f"{reciente['dias_del_mes']} días, o el mes anterior no está completo. Comparar totales de meses a medias da "
            f"variaciones sin sentido."
        )
    if reciente["variacion_promedio"] is None:
        notas.append("Tampoco hay un mes anterior con datos suficientes para comparar el promedio por día.")
    else:
        notas.append("El promedio por día sí se compara aunque el mes vaya a medias, porque promedia solo los días que ya tienen datos.")
    if parcial:
        notas.append(f"El mes más reciente está incompleto (datos hasta el {ultimo}).")
    st.caption(" ".join(notas))
    datos = gasto.serie_mensual(en_rango, medida)
    if any(d["monto"] for d in datos):
        st.altair_chart(
            gasto_grafico.construir(datos, moneda, _tema_oscuro(), [gasto.etiqueta_de_mes(m) for m in meses], "mes"),
            width="stretch", theme=None,
        )
    else:
        st.info("En estos meses no hay gasto en la medida elegida (los créditos lo cubren todo).")
    with st.expander("Ver tabla por mes"):
        st.dataframe(
            [{"mes": gasto.etiqueta_de_mes(r["mes"]) + (" (parcial)" if gasto.es_parcial(r["mes"], ultimo, hasta) else ""),
              "días con datos": f"{r['dias_con_datos']} de {r['dias_del_mes']}",
              f"bruto ({moneda})": round(r["bruto"], 4), f"créditos ({moneda})": round(r["creditos"], 4),
              f"neto ({moneda})": round(r["neto"], 4),
              f"bruto por día ({moneda})": round(r["promedio_diario_bruto"], 4),
              "variación del total": delta(r["variacion_bruto"]) or "—",
              "variación por día": delta(r["variacion_promedio"]) or "—"}
             for r in resumen],
            width="stretch", hide_index=True,
        )
    _detalle_conceptos(en_rango, moneda, "Ver detalle por concepto, SKU y región (estos meses)")
    con_datos = [r["mes"] for r in resumen]
    if len(con_datos) >= 2:
        col_a, col_b = st.columns(2)
        mes_ahora = col_a.selectbox("Explicar el mes", con_datos, index=len(con_datos) - 1, format_func=gasto.etiqueta_de_mes, key="cambio_mes_ahora")
        candidatos = [m for m in con_datos if m != mes_ahora]
        previo_sugerido = gasto.mes_anterior(mes_ahora)
        mes_antes = col_b.selectbox("contra el mes", candidatos, index=candidatos.index(previo_sugerido) if previo_sugerido in candidatos else len(candidatos) - 1,
                                    format_func=gasto.etiqueta_de_mes, key="cambio_mes_antes")
        _tabla_cambios(
            [f for f in filas if gasto.mes_de(f["dia"]) == mes_ahora], [f for f in filas if gasto.mes_de(f["dia"]) == mes_antes],
            moneda, f"¿Qué explica el cambio de {gasto.etiqueta_de_mes(mes_antes)} a {gasto.etiqueta_de_mes(mes_ahora)}? (promedio por día con datos)",
            por_dia=True,
        )

with tab_gastos:
    if not cfg:
        st.info("Primero configura la conexión.")
    elif not cfg.get("billing_tabla"):
        st.subheader("Gastos de Google Cloud")
        st.info(
            "Para ver el gasto por servicio hay que activar, una sola vez, la **exportación de facturación a BigQuery**:\n\n"
            "1. Consola de Google Cloud → **Facturación** → **Exportación de facturación** (*Billing export*) → "
            "pestaña **BigQuery export**.\n"
            "2. En *Standard usage cost* → **Edit settings** → elige tu proyecto y crea un conjunto de datos "
            "(por ejemplo `facturacion`) → **Save**.\n"
            "3. Espera unas horas: la exportación solo reúne datos desde que se activa, no trae el historial.\n"
            "4. En BigQuery abre ese conjunto de datos, copia el nombre de la tabla "
            "`gcp_billing_export_v1_…` y pégalo en la pestaña **Conexión** como `proyecto.dataset.tabla`.\n\n"
            "El almacenamiento y las consultas de BigQuery tienen una cuota gratis mensual muy por encima de lo que usa esto."
        )
    else:
        st.subheader("Gastos de Google Cloud")
        col_m, col_r = st.columns(2)
        medida = col_m.selectbox(
            "Medida", gasto.MEDIDAS,
            format_func=lambda m: "Bruto (valor de lo usado)" if m == "bruto" else "Neto (lo que se paga)",
        )
        try:
            todas = _gasto(cfg["billing_tabla"], cfg["project"])
        except Exception as e:
            _mostrar_error(e, "No se pudo consultar la facturación")
            todas = []
        if not todas:
            st.warning("La tabla de facturación todavía no tiene datos. La exportación puede tardar unas horas en llenarse.")
        else:
            moneda = gasto.moneda_principal(todas)
            todas = gasto.solo_moneda(todas, moneda)
            proyecto = col_r.selectbox("Proyecto", ["(todos)"] + gasto.proyectos(todas))
            filas = gasto.filtrar_proyecto(todas, None if proyecto == "(todos)" else proyecto)
            if not filas:
                st.info("Ese proyecto no tiene gasto registrado todavía.")
            else:
                # El último día con datos se mide sobre todo el historial (no sobre el proyecto filtrado): es lo que
                # dice hasta cuándo ha exportado Google. Los días van en UTC, como los agrupa la consulta.
                ultimo = max(f["dia"] for f in todas)
                hasta = max(datetime.now(timezone.utc).date(), date.fromisoformat(ultimo))
                tab_dias, tab_meses = st.tabs(["Últimos días", "Por mes"])
                with tab_dias:
                    _vista_dias(filas, medida, moneda, hasta, ultimo)
                with tab_meses:
                    _vista_meses(filas, medida, moneda, hasta, ultimo)
                _tabla_por_servicio(filas, moneda, "Ver tabla por servicio (todo el historial)")
                st.caption(
                    "Datos de la exportación de facturación a BigQuery: se actualizan con horas de retraso y se guardan "
                    "10 minutos en memoria. Los créditos del free tier descuentan el uso; por eso el neto suele ser mucho "
                    "menor que el bruto. Las líneas de ajuste de la factura (\"Invoice\") no se grafican, pero cuentan en los totales."
                )


# ---------------------------------------------------------------------------
# Limpieza
# ---------------------------------------------------------------------------
@st.cache_resource
def _almacen_raiz(destino: str, cfg_json: str):
    # La raíz del bucket (ve las carpetas de todos los usuarios): solo para la limpieza.
    return almacenamiento.crear_almacen(destino, credenciales=admin_nube.credenciales_admin(json.loads(cfg_json)))


def _analizar_bucket(cfg):
    """Lee el bucket y la lista de usuarios y guarda el análisis en la sesión (no se repite en cada clic)."""
    cfg_json = json.dumps(cfg, sort_keys=True)
    raiz = _almacen_raiz(f"gs://{cfg['bucket']}", cfg_json)
    datos = usuarios.cargar(_almacen_admin(admin_nube.destino_admin(cfg), cfg_json))  # sin lista legible, no se limpia nada
    ahora = time.time()
    analisis = limpieza.analizar(raiz.listar_detalle(), datos, ahora)
    analisis["colgados"] = limpieza.estados_colgados(raiz, analisis, ahora)
    analisis["vigentes"] = limpieza.ids_vigentes(datos)
    analisis["raiz"] = raiz
    analisis["a_las"] = ahora
    st.session_state["limpieza_analisis"] = analisis
    return analisis


def _bloque_borrado(clave, titulo, nombres, explicacion, analisis, cfg):
    """Un tipo de limpieza: lo que se borraría, y el botón, que exige confirmación explícita."""
    st.markdown(f"**{titulo}**")
    if not nombres:
        st.success("Nada que limpiar.")
        return
    st.write(explicacion)
    with st.expander(f"Ver los {len(nombres)} archivo(s) que se borrarían"):
        st.dataframe([{"archivo": n} for n in nombres], width="stretch", hide_index=True)
    entiendo = st.checkbox("Entiendo que se borra para siempre", key=f"ok_{clave}")
    palabra = st.text_input("Escribe BORRAR para confirmar", key=f"txt_{clave}")
    if st.button(f"Borrar ({len(nombres)})", key=f"btn_{clave}", disabled=not (entiendo and palabra.strip() == "BORRAR")):
        try:
            borrados = limpieza.ejecutar(analisis["raiz"], nombres, analisis["vigentes"])
            st.session_state["limpieza_msg"] = f"Listo: {borrados} archivo(s) borrado(s)."
            _analizar_bucket(cfg)
            # lo seleccionado y la confirmación ya no valen: se descartan para que no queden armados
            for k in (f"ok_{clave}", f"txt_{clave}", "limpieza_huerfanas"):
                st.session_state.pop(k, None)
            st.rerun()
        except (limpieza.ErrorLimpieza, almacenamiento.ErrorAlmacen) as e:
            st.error(str(e))


def _pestana_limpieza(cfg):
    st.subheader("Limpieza del bucket")
    st.caption(
        "Solo propone borrar lo que ya no sirve: carpetas de personas que ya no están en la lista de usuarios, candados y banderas "
        "temporales que quedaron colgados, y archivos de funciones que ya no existen. La lista de usuarios y todo lo de los "
        "usuarios vigentes están protegidos y no se pueden borrar desde aquí. Nada se borra sin tu confirmación."
    )
    mensaje = st.session_state.pop("limpieza_msg", None)
    if mensaje:
        st.success(mensaje)
    if st.button("Analizar el bucket ahora", type="primary"):
        try:
            analisis = _analizar_bucket(cfg)  # el botón siempre vuelve a leer el bucket
        except Exception as e:
            _mostrar_error(e, "No se pudo analizar el bucket")
            return
    elif "limpieza_analisis" in st.session_state:
        analisis = st.session_state["limpieza_analisis"]  # sin clic, se reutiliza el último de esta sesión
    else:
        st.info("Aprieta el botón para revisar qué hay en el bucket y qué se puede limpiar.")
        return

    k1, k2, k3 = st.columns(3)
    k1.metric("Archivos", analisis["objetos"])
    k2.metric("Tamaño total", f"{analisis['bytes'] / 1024 / 1024:.2f} MB")
    k3.metric("Del free tier de Cloud Storage (5 GB)", f"{analisis['porcentaje_gratis'] * 100:.3f} %")
    st.caption(f"Analizado a las {datetime.fromtimestamp(analisis['a_las']):%H:%M:%S}. Aprieta el botón de arriba para volver a leer el bucket.")

    st.markdown("**Carpetas de usuarios**")
    st.dataframe(
        [{"usuario": c["correo"] or "(ya no está en la lista)", "estado": "vigente" if c["vigente"] else "huérfana",
          "archivos": c["objetos"], "tamaño (KB)": round(c["bytes"] / 1024, 1),
          "última actividad": datetime.fromtimestamp(c["ultima_actividad"]).strftime("%Y-%m-%d %H:%M")}
         for c in analisis["carpetas"]],
        width="stretch", hide_index=True,
    )

    st.divider()
    huerfanas = {c["id"]: c for c in analisis["huerfanas"]}
    elegidas = st.multiselect(
        "Carpetas de personas que ya no están en la lista", sorted(huerfanas), default=sorted(huerfanas),
        format_func=lambda i: f"{i}  ({huerfanas[i]['objetos']} archivos, {huerfanas[i]['bytes'] / 1024:.1f} KB)",
        key="limpieza_huerfanas",
    ) if huerfanas else []
    _bloque_borrado(
        "huerfanas", "1. Carpetas huérfanas", [n for i in elegidas for n in huerfanas[i]["nombres"]],
        "Son datos de personas que ya no están en la lista de usuarios (o de pruebas viejas). Antes de borrar, revisa que no sea alguien a quien quitaste por error.",
        analisis, cfg,
    )
    st.divider()
    _bloque_borrado("temporales", "2. Candados y banderas colgados", analisis["temporales_vencidos"],
                    "Archivos efímeros (candado de lanzamiento, bandera de detener) con más de un día: es seguro quitarlos.", analisis, cfg)
    st.divider()
    _bloque_borrado("obsoletos", "3. Archivos de funciones que ya no existen", analisis["obsoletos"],
                    "Quedaron de una función que se quitó de la herramienta.", analisis, cfg)
    st.divider()
    _bloque_borrado("colgados", "4. Estados de scans que murieron sin cerrarse", analisis["colgados"],
                    "El estado dice \"corriendo\" pero hace más de un día que no da señales. Al borrarlo, la pantalla de esa persona queda limpia (sus resultados no se tocan).",
                    analisis, cfg)

    st.divider()
    st.markdown("**Limpieza automática de lo efímero**")
    try:
        activa = admin_nube.regla_de_temporales_activa(cfg, admin_nube.credenciales_admin(cfg))
    except Exception as e:
        _mostrar_error(e, "No se pudo leer la configuración del bucket")
        return
    if activa:
        st.success("Activa: Google borra solo los candados y banderas con más de un día.")
    else:
        st.write(
            "Una regla del bucket puede borrar sola, sin que hagas nada, los candados y banderas temporales con más de un día. "
            "Solo afecta a esos archivos efímeros: los datos de los usuarios nunca van en una regla automática."
        )
        if st.button("Activar la limpieza automática"):
            try:
                admin_nube.activar_regla_de_temporales(cfg, admin_nube.credenciales_admin(cfg))
                st.session_state["limpieza_msg"] = "Regla activada."
                st.rerun()
            except Exception as e:
                _mostrar_error(e, "No se pudo activar")
    st.caption(
        "Google guarda lo que borras durante unos días (borrado suave) antes de eliminarlo del todo, por si fue un error; en ese plazo "
        "se puede recuperar desde la consola de Google Cloud."
    )


with tab_limpieza:
    if not cfg:
        st.info("Primero configura la conexión.")
    else:
        _pestana_limpieza(cfg)
