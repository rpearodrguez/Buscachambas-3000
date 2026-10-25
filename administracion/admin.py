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

import os
import sys

import streamlit as st

# Los puntos de entrada viven en subcarpetas: la raíz del repo va en sys.path
# para poder importar los paquetes hermanos (comun, escaner, ...).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)  # admin_local.json y demás archivos locales viven en la raíz del repo

from administracion import admin_nube, gasto, gasto_grafico  # noqa: E402
from comun import almacen as almacenamiento, usuarios  # noqa: E402

st.set_page_config(page_title="Administración — Job Scanner", layout="wide")
st.title("Administración")
st.caption("Solo para el administrador, corre en tu PC con tus credenciales de Google. No se publica.")


def _mostrar_error(e: Exception, contexto: str):
    st.error(f"{contexto}: {e}")


cfg = admin_nube.cargar_config_local()

tab_conexion, tab_correo, tab_usuarios, tab_gastos = st.tabs(["Conexión", "Correo (SMTP)", "Usuarios", "Gastos"])

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
                almacenamiento.crear_almacen(admin_nube.destino_admin(cfg))
                st.success("Bucket accesible y privado.")
                actual_smtp = admin_nube.leer_smtp_del_job(cfg)
                st.success(f"Job encontrado (servidor SMTP actual: {actual_smtp['host'] or 'sin configurar'}).")
            except Exception as e:  # es la PC del administrador: se muestra el detalle
                _mostrar_error(e, "Falló la conexión")
    else:
        st.info("Completa y guarda la conexión para usar las otras pestañas.")


@st.cache_resource
def _almacen_admin(destino: str):
    return almacenamiento.crear_almacen(destino)


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
def _pestana_usuarios(cfg):
    st.subheader("Usuarios autorizados")
    try:
        almacen_admin = _almacen_admin(admin_nube.destino_admin(cfg))
        datos = usuarios.cargar(almacen_admin)
    except Exception as e:
        _mostrar_error(e, "No se pudo leer la lista de usuarios")
        return  # solo esta pestaña falla; las demás siguen

    if datos["usuarios"]:
        st.dataframe(
            [{"correo": c, "rol": u.get("rol", "usuario"), "cuota semanal": u.get("cuota_semanal", usuarios.CUOTA_SEMANAL_POR_DEFECTO)}
             for c, u in sorted(datos["usuarios"].items())],
            width="stretch", hide_index=True,
        )
    else:
        st.warning("Todavía no hay usuarios. Agrega primero tu propio correo como administrador.")

    with st.form("form_agregar_usuario"):
        st.markdown("**Agregar o actualizar un usuario**")
        correo = st.text_input("Correo (el que usa para entrar con Google)")
        col_r, col_c = st.columns(2)
        rol = col_r.selectbox("Rol", usuarios.ROLES)
        cuota = col_c.number_input("Scans por semana", value=usuarios.CUOTA_SEMANAL_POR_DEFECTO, min_value=0, max_value=usuarios.CUOTA_MAXIMA, step=1)
        if st.form_submit_button("Guardar usuario"):
            try:
                usuarios.guardar(almacen_admin, usuarios.agregar(datos, correo, rol, cuota))
                st.rerun()
            except usuarios.ErrorUsuarios as e:
                st.error(str(e))
            except Exception as e:
                _mostrar_error(e, "No se pudo guardar")

    if datos["usuarios"]:
        with st.form("form_quitar_usuario"):
            st.markdown("**Quitar un usuario**")
            quitar = st.selectbox("Usuario", sorted(datos["usuarios"]))
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
def _gasto(cfg_tabla: str, proyecto: str, dias: int):
    # cfg_tabla/proyecto entran como argumentos para que formen parte de la clave de la caché
    return admin_nube.consultar_gasto({"billing_tabla": cfg_tabla, "project": proyecto}, dias)


def _tema_oscuro() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return True


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
        col_p, col_m, col_r = st.columns(3)
        dias = col_p.selectbox("Período", [7, 30, 90], index=1, format_func=lambda d: f"Últimos {d} días")
        medida = col_m.selectbox(
            "Medida", gasto.MEDIDAS,
            format_func=lambda m: "Bruto (valor de lo usado)" if m == "bruto" else "Neto (lo que se paga)",
        )
        try:
            todas = _gasto(cfg["billing_tabla"], cfg["project"], dias)
        except Exception as e:
            _mostrar_error(e, "No se pudo consultar la facturación")
            todas = []
        if not todas:
            st.warning("Todavía no hay datos de facturación en ese período. La exportación puede tardar unas horas en llenarse.")
        else:
            moneda = gasto.moneda_principal(todas)
            todas = gasto.solo_moneda(todas, moneda)
            proyectos = gasto.proyectos(todas)
            proyecto = col_r.selectbox("Proyecto", ["(todos)"] + proyectos)
            filas = gasto.filtrar_proyecto(todas, None if proyecto == "(todos)" else proyecto)

            t = gasto.totales(filas)
            k1, k2, k3 = st.columns(3)
            k1.metric("Se paga (neto)", gasto.formatear(t["neto"], moneda))
            k2.metric("Valor de lo usado (bruto)", gasto.formatear(t["bruto"], moneda))
            k3.metric("Cubierto por créditos / free tier", gasto.formatear(-t["creditos"], moneda))

            datos = gasto.serie_diaria(filas, medida)
            if datos and any(d["monto"] for d in datos):
                st.altair_chart(gasto_grafico.construir(datos, moneda, _tema_oscuro()), width="stretch", theme=None)
            else:
                st.info("En este período no hay gasto en la medida elegida (los créditos lo cubren todo).")

            with st.expander("Ver tabla por servicio"):
                st.dataframe(
                    [{"servicio": r["servicio"], f"bruto ({moneda})": round(r["bruto"], 4),
                      f"créditos ({moneda})": round(r["creditos"], 4), f"neto ({moneda})": round(r["neto"], 4)}
                     for r in gasto.tabla_por_servicio(filas)],
                    width="stretch", hide_index=True,
                )
            st.caption(
                "Datos de la exportación de facturación a BigQuery: se actualizan con horas de retraso y se guardan "
                "10 minutos en memoria. Los créditos del free tier descuentan el uso; por eso el neto suele ser mucho menor que el bruto."
            )
