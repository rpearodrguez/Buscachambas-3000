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
    streamlit run admin.py --server.address localhost

Los datos de conexión (proyecto, región, job, bucket) se guardan en
admin_local.json, que está en .gitignore: el repo es público y nada de eso
va ahí. El token SMTP se escribe sin mostrarse, va directo a Secret Manager y
no se puede volver a leer desde aquí.
"""

import streamlit as st

import admin_nube
import almacen as almacenamiento
import usuarios

st.set_page_config(page_title="Administración — Job Scanner", layout="wide")
st.title("Administración")
st.caption("Solo para el administrador, corre en tu PC con tus credenciales de Google. No se publica.")


def _mostrar_error(e: Exception, contexto: str):
    st.error(f"{contexto}: {e}")


cfg = admin_nube.cargar_config_local()

tab_conexion, tab_correo, tab_usuarios = st.tabs(["Conexión", "Correo (SMTP)", "Usuarios"])

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
        if st.form_submit_button("Guardar"):
            try:
                admin_nube.guardar_config_local(
                    {"project": project, "region": region, "job": job, "bucket": bucket, "secret": secret}
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
with tab_usuarios:
    if not cfg:
        st.info("Primero configura la conexión.")
    else:
        st.subheader("Usuarios autorizados")
        try:
            almacen_admin = _almacen_admin(admin_nube.destino_admin(cfg))
            datos = usuarios.cargar(almacen_admin)
        except Exception as e:
            _mostrar_error(e, "No se pudo leer la lista de usuarios")
            st.stop()

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
