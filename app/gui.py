"""
GUI (Streamlit) del Job Scanner
--------------------------------
Interfaz web local para correr escaner/scan_getonbrd.py y escaner/extraer_oferta.py sin
tocar la línea de comandos: elegir país/sitios/keywords, correr el scan
con barra de progreso, filtrar y exportar resultados, y extraer el texto
completo de una oferta puntual.

El scan corre como un PROCESO APARTE del sistema operativo (no un hilo
dentro de Streamlit): esta página lo lanza con subprocess.Popen y desde
ahí solo lee su progreso desde un archivo JSON (scan_status.json) que el
propio escaner/scan_getonbrd.py va escribiendo. Esto es a propósito — así el scan
sigue corriendo aunque cierres el navegador, reinicies Streamlit, o esté
corriendo mientras se edita el código: no depende de objetos en memoria
de esta página, que se pierden en cualquiera de esos casos.

USO:
    pip install streamlit pandas pypdf python-docx pyperclip
    streamlit run app/gui.py
"""

import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# Los puntos de entrada viven en subcarpetas: la raíz del repo va en sys.path
# para poder importar los paquetes hermanos (comun, escaner, ...).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)  # estado, CSV, keywords y logs locales viven en la raíz del repo, no en app/

from comun import acceso, lanzador, usuarios  # noqa: E402
from comun import almacen as almacenamiento  # noqa: E402
from escaner import extraer_oferta as extractor  # noqa: E402
from escaner import scan_getonbrd as scanner  # noqa: E402

STATUS_FILE = "scan_status.json"
DETENER_FILE = "detener.flag"
KEYWORDS_GUI_FILE = "keywords_gui_actual.txt"
SCAN_LOG_FILE = "scan_stdout.log"

# Dónde viven los archivos compartidos con el scan (estado, flag de detener,
# CSV, keywords). Sin variables de entorno / secrets, archivos en disco como
# siempre y sin login (tu propia PC). Con BUSCACHAMBAS_JOB y
# BUSCACHAMBAS_STORAGE=gs://bucket el scan corre en la nube: hay login con
# Google, solo entran los correos de la lista de usuarios, y cada uno tiene su
# carpeta del bucket (derivada de su correo en el servidor, nunca elegida por
# el usuario).
USUARIO_LOCAL = "local"


def _config(nombre: str) -> str | None:
    """Variable de entorno o, si no está, secret de Streamlit. Nada de esto
    va al repo (es público): ni el bucket, ni el job, ni las credenciales."""
    valor = os.environ.get(nombre)
    if valor:
        return valor
    try:
        return st.secrets.get(nombre)
    except Exception:  # sin archivo de secrets (uso local)
        return None


def _credenciales_gcp():
    """Credenciales de la cuenta de servicio de la GUI (secret
    "gcp_service_account" en Streamlit Cloud). Sin él, se usan las del
    entorno (gcloud / ADC), útil para probar desde tu PC."""
    try:
        info = dict(st.secrets["gcp_service_account"])
    except Exception:
        info = None
    from google.oauth2 import service_account
    if info:  # una sección vacía (p. ej. la plantilla sin rellenar) cuenta como ausente
        return service_account.Credentials.from_service_account_info(info)
    # Alternativa para tu PC: ruta del archivo de la llave en BUSCACHAMBAS_GCP_KEYFILE
    # (así no hay que definir GOOGLE_APPLICATION_CREDENTIALS en cada ventana nueva).
    ruta = _config("BUSCACHAMBAS_GCP_KEYFILE")
    if ruta:
        try:
            return service_account.Credentials.from_service_account_file(os.path.expanduser(ruta))
        except (OSError, ValueError) as e:
            # El detalle (con la ruta) solo al log del servidor, no a la pantalla.
            print(f"[gui] No se pudo leer el archivo de credenciales configurado: {type(e).__name__}", file=sys.stderr)
    return None


# BUSCACHAMBAS_MODO=local fuerza el modo local (archivos en esta carpeta, sin login ni
# cuotas) aunque .streamlit/secrets.toml tenga la configuración de la nube: es lo que
# usa iniciar_gui.cmd, para que escanear desde tu PC siga igual que siempre.
MODO_LOCAL = os.environ.get("BUSCACHAMBAS_MODO", "").strip().lower() == "local"
STORAGE_BASE = None if MODO_LOCAL else _config("BUSCACHAMBAS_STORAGE")
# Si está definido, el scan corre como Cloud Run Job (projects/../jobs/..) en
# vez de un subproceso local; requiere STORAGE_BASE para compartir el estado.
JOB_NAME = None if MODO_LOCAL else _config("BUSCACHAMBAS_JOB")
try:
    MAX_SIMULTANEOS = int(_config("BUSCACHAMBAS_MAX_SIMULTANEOS") or acceso.MAX_SIMULTANEOS_POR_DEFECTO)
except ValueError:
    MAX_SIMULTANEOS = acceso.MAX_SIMULTANEOS_POR_DEFECTO

st.set_page_config(page_title="Job Scanner", layout="wide")
st.title("Job Scanner")


# Cacheado: crear el almacén de un bucket verifica que sea privado (una
# consulta a la API), y esta página se re-ejecuta entera cada segundo
# mientras corre un scan.
@st.cache_resource
def _crear_almacen(destino):
    return almacenamiento.crear_almacen(destino, credenciales=_credenciales_gcp())


def _almacen_o_detener(destino):
    try:
        return _crear_almacen(destino)
    except almacenamiento.ErrorAlmacen as e:
        st.error(str(e))
        st.stop()


@st.cache_data(ttl=30, show_spinner=False)
def _lista_de_usuarios(_almacen_admin):
    # ttl corto: un usuario nuevo o quitado desde administracion/admin.py se nota en segundos,
    # sin leer el bucket en cada re-ejecución de la página.
    return usuarios.cargar(_almacen_admin)


if JOB_NAME and not STORAGE_BASE:
    st.error("Configuración incompleta: el escaneo en la nube necesita almacenamiento compartido.")
    st.stop()

usuario_email = None
info_usuario = None
USUARIO_ID = USUARIO_LOCAL
if JOB_NAME:
    # --- login y lista de usuarios (todo falla cerrado) ---
    try:
        login_configurado = "auth" in st.secrets
    except Exception:
        login_configurado = False
    if not login_configurado:
        st.error("El inicio de sesión no está configurado en el servidor.")
        st.stop()
    try:
        sesion_iniciada = bool(st.user.is_logged_in)
    except Exception:
        sesion_iniciada = False  # ante la duda, sin sesión
    if not sesion_iniciada:
        st.info("Inicia sesión con tu cuenta de Google para continuar.")
        st.button("Iniciar sesión con Google", on_click=st.login)
        st.stop()

    almacen_admin = _almacen_o_detener(f"{STORAGE_BASE.rstrip('/')}/admin")
    try:
        lista_usuarios = _lista_de_usuarios(almacen_admin)
    except (usuarios.ErrorUsuarios, almacenamiento.ErrorAlmacen):
        st.error("No se pudo verificar tu acceso en este momento. Inténtalo más tarde.")
        st.stop()

    motivo_acceso, info_usuario = acceso.decidir_acceso(
        st.user.get("email"), st.user.get("email_verified"), lista_usuarios
    )
    if motivo_acceso != "ok":
        if motivo_acceso == "no_autorizado":
            st.error("Tu cuenta no tiene acceso a esta herramienta. Pídele acceso al administrador.")
        else:
            st.error("No se pudo verificar tu cuenta de Google.")
        st.button("Cerrar sesión", on_click=st.logout)
        st.stop()

    usuario_email = usuarios.normalizar_correo(st.user.get("email"))
    USUARIO_ID = almacenamiento.id_usuario(usuario_email)
    st.sidebar.caption(f"Sesión iniciada como {usuario_email}")
    st.sidebar.button("Cerrar sesión", on_click=st.logout)

STORAGE_DESTINO = almacenamiento.destino_de_usuario(STORAGE_BASE, USUARIO_ID)
almacen = _almacen_o_detener(STORAGE_DESTINO)


def leer_estado_scan() -> dict | None:
    # El scan reescribe este archivo en cada request (más seguido todavía
    # en la fase oferta por oferta), así que una lectura acá puede chocar
    # con el rename atómico del lado del scanner — sin reintentar, esa
    # única lectura fallida corta el loop de auto-refresco de la página
    # para siempre (queda "corriendo" en falso aunque el scan siga vivo).
    for intento in range(5):
        try:
            texto = almacen.leer_texto(STATUS_FILE)
            return None if texto is None else json.loads(texto)
        except (json.JSONDecodeError, OSError):
            if intento == 4:
                return None
            time.sleep(0.05 * (intento + 1))


def proceso_sigue_vivo(pid: int) -> bool:
    """Chequea si el PID del scan sigue corriendo de verdad. Si el proceso
    murió (crash, se cerró la ventana, el equipo se durmió, etc.) sin
    llegar a marcarse como terminado, scan_status.json queda con
    "corriendo": true para siempre — sin este chequeo la GUI se queda
    mostrando una barra de progreso que nunca va a avanzar."""
    try:
        resultado = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in resultado.stdout
    except Exception:
        return True  # si no se puede chequear, no bloquear la UI por las dudas


def lanzar_scan(pais, ids_sitios, keywords, require_remote, descripcion_completa, email_a=None):
    almacen.escribir_texto(KEYWORDS_GUI_FILE, "\n".join(keywords) + "\n")
    almacen.borrar(DETENER_FILE)

    argumentos = [
        "--pais", pais,
        "--solo", ",".join(ids_sitios),
        "--keywords-file", KEYWORDS_GUI_FILE,
        "--status-file", STATUS_FILE,
        "--detener-file", DETENER_FILE,
    ]
    if not require_remote:
        argumentos.append("--incluir-no-remotas")
    if descripcion_completa:
        argumentos.append("--descripcion-completa")
    if email_a:
        argumentos += ["--email-a", email_a]
    if STORAGE_DESTINO:
        argumentos += ["--storage", STORAGE_DESTINO]

    if JOB_NAME:
        # El job tarda en arrancar: dejar ya escrito un estado "corriendo"
        # para que la página lo muestre de inmediato (el scan lo pisa al
        # arrancar). Si el job no llega a arrancar, queda sin latido y la
        # GUI lo marca como cortado (ver lanzador.scan_sin_latido).
        # La hora de inicio también viaja al job (--inicio-ts), así el
        # contador de tiempo no se reinicia cuando el job toma el relevo.
        inicio = time.time()
        argumentos += ["--inicio-ts", repr(inicio)]
        nombres = [scanner.SITIOS_POR_ID[i]["nombre"] for i in ids_sitios]
        almacen.escribir_texto(STATUS_FILE, json.dumps(scanner.estado_inicial(pais, nombres, ahora=inicio, pid=0), ensure_ascii=False))
        lanzador.lanzar_job(JOB_NAME, argumentos, credenciales=_credenciales_gcp())
        return

    cmd = [sys.executable, os.path.join(RAIZ, "escaner", "scan_getonbrd.py")] + argumentos

    # stdout/stderr a un archivo (no DEVNULL): si el proceso crashea con una
    # excepción no manejada, scan_status.json queda con "corriendo": true
    # para siempre (nunca llega a escribirse el resultado final) y sin este
    # log no hay forma de saber por qué murió.
    log = open(SCAN_LOG_FILE, "w", encoding="utf-8")
    subprocess.Popen(
        cmd,
        cwd=RAIZ,
        stdout=log,
        stderr=log,
    )


def extraer_texto_documento(archivo) -> str:
    """Saca el texto de un CV subido (.txt, .pdf o .docx) para usarlo como
    perfil en el prompt de keywords."""
    nombre = archivo.name.lower()
    datos = archivo.read()

    if nombre.endswith(".txt") or nombre.endswith(".md"):
        return datos.decode("utf-8", errors="ignore")

    if nombre.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(datos))
        return "\n".join(pagina.extract_text() or "" for pagina in reader.pages)

    if nombre.endswith(".docx"):
        from docx import Document
        doc = Document(io.BytesIO(datos))
        return "\n".join(p.text for p in doc.paragraphs)

    return ""

# ---------------------------------------------------------------------------
# Sidebar: configuración del scan
# ---------------------------------------------------------------------------

def _ofrecer_envio_manual(estado):
    """Botón para mandar por correo el resultado de un escaneo ya terminado, por si se olvidó marcar el aviso.
    Una sola vez por escaneo (una marca atómica en el almacenamiento lo impide aunque se haga doble clic o se
    abra otra pestaña), y siempre al correo del propio usuario. En la nube lo manda el job (la GUI pública no
    tiene la contraseña del correo); en local, la propia GUI."""
    destino = usuario_email if JOB_NAME else (config_email.get("destinatario") if email_listo else None)
    if not destino:
        st.caption("Para recibir este resultado por correo, configura el aviso en la barra lateral.")
        return
    inicio = estado.get("inicio_ts")
    if not inicio:
        return
    if almacen.existe(scanner.nombre_marca_correo(inicio)):
        st.caption("✉ Ya se pidió el envío de este resultado por correo (solo se permite una vez por escaneo).")
        return
    if not st.button(f"✉ Enviar este resultado por correo a {destino}", key="enviar_manual"):
        return
    try:
        if not scanner.crear_marca_correo(almacen, inicio):
            st.warning("Este resultado ya se pidió enviar por correo.")
            return
        if JOB_NAME:
            try:
                lanzador.lanzar_job(
                    JOB_NAME,
                    ["--enviar-resultado", destino, "--inicio-ts", repr(inicio), "--status-file", STATUS_FILE, "--storage", STORAGE_DESTINO],
                    credenciales=_credenciales_gcp(),
                )
            except lanzador.ErrorLanzador:
                almacen.borrar(scanner.nombre_marca_correo(inicio))  # no salió nada: se puede reintentar
                raise
            st.success("Listo: el correo llega en un minuto.")
        else:
            error = scanner.reenviar_resultado(almacen, destino, inicio)
            if error:
                almacen.borrar(scanner.nombre_marca_correo(inicio))
                st.error(f"No se pudo enviar el correo: {error}")
            else:
                st.rerun()
    except (lanzador.ErrorLanzador, almacenamiento.ErrorAlmacen) as e:
        st.error(str(e))


PREFS_FILE = "preferencias.json"


def _cargar_prefs() -> dict:
    """Elecciones guardadas del usuario (país, sitios, solo remotas, aviso por correo).
    Las keywords NO van acá: solo se conservan si el usuario las guarda con el botón."""
    try:
        datos = json.loads(almacen.leer_texto(PREFS_FILE) or "{}")
    except (ValueError, almacenamiento.ErrorAlmacen):
        return {}
    return datos if isinstance(datos, dict) else {}


def _guardar_prefs(prefs: dict) -> None:
    try:
        almacen.escribir_texto(PREFS_FILE, json.dumps(prefs, ensure_ascii=False))
    except almacenamiento.ErrorAlmacen:
        pass  # una preferencia que no se guarda no debe romper la página


def _keywords_guardadas() -> str:
    """Las keywords que el usuario guardó con el botón (keywords.txt de su carpeta); vacío si nunca guardó."""
    try:
        texto = almacen.leer_texto(scanner.KEYWORDS_FILE_DEFAULT) or ""
    except almacenamiento.ErrorAlmacen:
        return ""
    return "\n".join(l.strip() for l in texto.splitlines() if l.strip() and not l.strip().startswith("#"))


# Estado inicial de la sesión, una sola vez. En la nube, un usuario nuevo parte VACÍO (sin las
# keywords de nadie) y con todo seleccionado; si ya usó la herramienta, vuelven sus elecciones.
if "_iniciado" not in st.session_state:
    prefs = _cargar_prefs() if JOB_NAME else {}
    # Un usuario sin elecciones guardadas parte en el país que el administrador le asignó (si lo hizo).
    pais_inicial = (
        prefs.get("pais") if prefs.get("pais") in scanner.PAISES
        else usuarios.pais_por_defecto(info_usuario, scanner.PAISES) or next(iter(scanner.PAISES))
    )
    ids_pais = scanner.PAISES[pais_inicial]
    if isinstance(prefs.get("sitios"), list):
        sitios_iniciales = [i for i in prefs["sitios"] if i in ids_pais]
    else:
        sitios_iniciales = list(ids_pais)
    st.session_state.update({
        "pais_sel": pais_inicial,
        "_pais_prev": pais_inicial,
        "sitios_sel": sitios_iniciales,
        "solo_remotas_sel": bool(prefs.get("solo_remotas", scanner.REQUIRE_REMOTE)),
        "aviso_correo_sel": bool(prefs.get("aviso_correo", False)),
        "keywords_text": _keywords_guardadas() if JOB_NAME else "\n".join(scanner.cargar_keywords(almacen=almacen)),
        "prefs_guardadas": prefs or None,
        "_iniciado": True,
    })

st.sidebar.header("Configuración")

with st.sidebar.expander("País y sitios", expanded=True):
    pais = st.selectbox("País", list(scanner.PAISES.keys()), key="pais_sel")
    ids_disponibles = scanner.PAISES[pais]
    if st.session_state["_pais_prev"] != pais:  # cambió el país: los sitios disponibles son otros
        st.session_state["sitios_sel"] = list(ids_disponibles)
        st.session_state["_pais_prev"] = pais
    sitios_elegidos = st.multiselect(
        "Sitios a escanear",
        options=ids_disponibles,
        format_func=lambda i: scanner.SITIOS_POR_ID[i]["nombre"],
        key="sitios_sel",
    )
    require_remote = st.checkbox("Solo ofertas remotas", key="solo_remotas_sel")

with st.sidebar.expander("Aviso por correo"):
    if JOB_NAME:
        # En la nube el SMTP (cuenta y contraseña) lo administra solo el
        # administrador, desde administracion/admin.py. Y el aviso va siempre al correo con el
        # que el usuario inició sesión: no hay un campo libre de destinatario
        # (permitiría usar la cuenta de envío para escribirle a terceros).
        config_email = {"destinatario": usuario_email}
        email_listo = True
        enviar_correo = st.checkbox(f"Avisarme por correo a {usuario_email} al terminar", key="aviso_correo_sel")
        guardar = probar = False
    else:
        config_email = scanner.cargar_config_email() or {}
        email_listo = scanner.config_email_completa(config_email)

        enviar_correo = st.checkbox(
            "Enviar correo al terminar el scan", value=False, disabled=not email_listo,
            help=None if email_listo else "Completa y guarda la configuración de abajo primero.",
        )
        if email_listo:
            st.caption(f"Se enviará a {config_email['destinatario']}")

    if not JOB_NAME:
        with st.form("form_email"):
            email_destino = st.text_input("Correo que recibirá el aviso", value=config_email.get("destinatario", ""))
            st.caption("Cuenta desde la que se envía (SMTP). Gmail: smtp.gmail.com, puerto 587, y una contraseña de aplicación.")
            email_host = st.text_input("Servidor SMTP", value=config_email.get("host", "smtp.gmail.com"))
            email_port = st.number_input("Puerto", value=int(config_email.get("port", 587)), min_value=1, max_value=65535, step=1)
            email_usuario = st.text_input("Usuario (correo de envío)", value=config_email.get("usuario", ""))
            email_password = st.text_input(
                "Contraseña", type="password",
                placeholder="(guardada — déjala vacía para no cambiarla)" if config_email.get("password") else "",
            )
            col_guardar, col_probar = st.columns(2)
            guardar = col_guardar.form_submit_button("Guardar")
            probar = col_probar.form_submit_button("Guardar y probar")

        if guardar or probar:
            nueva_config = {
                "host": email_host.strip(),
                "port": int(email_port),
                "usuario": email_usuario.strip(),
                "password": email_password or config_email.get("password", ""),
                "destinatario": email_destino.strip(),
            }
            nueva_config["remitente"] = config_email.get("remitente", "")
            scanner.guardar_config_email(nueva_config)
            if not scanner.config_email_completa(nueva_config):
                mensaje = ("warning", "Guardado, pero faltan campos — el aviso no se activa hasta completarlos todos.")
            elif probar:
                error = scanner.enviar_correo_fin(
                    nueva_config["destinatario"], "Job Scanner — correo de prueba",
                    "Si lees esto, el aviso por correo del Job Scanner está bien configurado.",
                )
                if error:
                    mensaje = ("error", f"Guardado, pero no se pudo enviar el correo de prueba: {error}")
                else:
                    mensaje = ("success", f"Guardado. Correo de prueba enviado a {nueva_config['destinatario']}")
            else:
                mensaje = ("success", "Guardado.")
            # Rerun para que el checkbox y "Se enviará a..." de arriba lean la
            # config recién guardada; el mensaje sobrevive vía session_state.
            st.session_state["email_msg"] = mensaje
            st.rerun()

        if "email_msg" in st.session_state:
            tipo, texto = st.session_state.pop("email_msg")
            getattr(st, tipo)(texto)

with st.sidebar.expander("Keywords", expanded=True):
    keywords_text = st.text_area("Una por línea", height=180, key="keywords_text")
    keywords = [k.strip() for k in keywords_text.splitlines() if k.strip()]

    if st.button("Guardar keywords en keywords.txt"):
        almacen.escribir_texto(scanner.KEYWORDS_FILE_DEFAULT, "\n".join(keywords) + "\n")
        st.success(f"Guardado en {almacen.descripcion(scanner.KEYWORDS_FILE_DEFAULT)}")


with st.sidebar.expander("Generar keywords con Claude"):
    archivo_cv = st.file_uploader("Subir CV (PDF, DOCX, TXT o MD)", type=["pdf", "docx", "txt", "md"])
    perfil_manual = st.text_area("...o pega tu perfil / CV acá", height=120)

    perfil = ""
    if archivo_cv is not None:
        perfil = extraer_texto_documento(archivo_cv)
        if perfil.strip():
            st.caption(f"✓ {len(perfil)} caracteres extraídos de {archivo_cv.name}")
        else:
            st.warning(f"No se pudo extraer texto de {archivo_cv.name}. Prueba pegando el texto abajo.")
    elif perfil_manual.strip():
        perfil = perfil_manual

    if st.button("Generar prompt"):
        st.session_state["prompt_keywords"] = scanner.construir_prompt_keywords(perfil)

    if "prompt_keywords" in st.session_state:
        st.code(st.session_state["prompt_keywords"], language="text")
        if st.button("📋 Copiar prompt al portapapeles"):
            import pyperclip
            try:
                pyperclip.copy(st.session_state["prompt_keywords"])
                st.toast("Copiado al portapapeles")
            except Exception as e:
                st.warning(f"No se pudo copiar automático ({e}) — usa el ícono de copiar del bloque de código de arriba.")
        st.caption("Pégalo en Claude, y pega la respuesta arriba en 'Keywords'.")

# ---------------------------------------------------------------------------
# Botón de scan (corre como proceso aparte — ver leer_estado_scan/lanzar_scan
# arriba. El estado "¿está corriendo?" se lee siempre del archivo en disco,
# nunca de session_state, para que sobreviva a reinicios de esta página.)
# ---------------------------------------------------------------------------

if JOB_NAME:
    prefs_actuales = {
        "pais": pais,
        "sitios": list(sitios_elegidos),
        "solo_remotas": bool(require_remote),
        "aviso_correo": bool(enviar_correo),
    }
    if prefs_actuales != st.session_state.get("prefs_guardadas"):
        _guardar_prefs(prefs_actuales)
        st.session_state["prefs_guardadas"] = prefs_actuales

estado_previo = leer_estado_scan()
marcado_corriendo = bool(estado_previo and estado_previo.get("corriendo"))
if JOB_NAME:
    # El scan corre en otra máquina: no hay PID que consultar, solo si dejó
    # de actualizar su estado.
    proceso_muerto = marcado_corriendo and lanzador.scan_sin_latido(estado_previo)
else:
    proceso_muerto = marcado_corriendo and not proceso_sigue_vivo(estado_previo.get("pid", -1))
scan_corriendo = marcado_corriendo and not proceso_muerto

col_buscar_completa, col_buscar, col_detener = st.columns([1, 1, 1])
with col_buscar_completa:
    click_buscar_completa = st.button(
        "📄 Buscar con descripción completa",
        type="primary",
        disabled=not sitios_elegidos or scan_corriendo,
        help="Para los sitios que lo soportan (todos salvo SENA), visita cada oferta aceptada para sacar su descripción real. Más lento que 'Buscar ofertas'.",
    )
with col_buscar:
    click_buscar = st.button("🔍 Buscar ofertas", disabled=not sitios_elegidos or scan_corriendo)
with col_detener:
    click_detener = st.button("⏹ Detener", disabled=not scan_corriendo)

if click_buscar or click_buscar_completa:
    if not keywords:
        st.error("No hay keywords para buscar.")
    else:
        lock_tomado = False
        try:
            if JOB_NAME:
                # Reglas de la nube, en orden: cuota semanal, tope global de
                # escaneos simultáneos y candado contra doble clic. Cualquier
                # duda corta (ver comun/acceso.py).
                ahora = time.time()
                puede, motivo, vuelve = acceso.evaluar_cuota(info_usuario, acceso.cargar_usos(almacen), ahora)
                if not puede:
                    cuando = f" Podrás volver a escanear el {datetime.fromtimestamp(vuelve):%d/%m/%Y a las %H:%M}." if vuelve else ""
                    st.error(motivo + cuando)
                    st.stop()
                if acceso.contar_scans_activos(_almacen_o_detener(STORAGE_BASE), ahora) >= MAX_SIMULTANEOS:
                    st.error("Hay varios escaneos en curso en este momento. Inténtalo de nuevo en unos minutos.")
                    st.stop()
                lock_tomado = acceso.adquirir_lock(almacen, ahora)
                if not lock_tomado:
                    st.error("Ya hay un escaneo iniciándose. Espera unos segundos.")
                    st.stop()
            lanzar_scan(
                pais, sitios_elegidos, keywords, require_remote, click_buscar_completa,
                email_a=config_email["destinatario"] if enviar_correo and email_listo else None,
            )
            if JOB_NAME:
                acceso.registrar_uso(almacen, ahora)  # solo cuenta si el job se lanzó
        except (lanzador.ErrorLanzador, almacenamiento.ErrorAlmacen) as e:
            st.error(str(e))
            st.stop()
        finally:
            if lock_tomado:
                acceso.liberar_lock(almacen)
        # Esperar a que el subproceso escriba su primer status.json (el
        # arranque de Python en Windows puede tardar más que un sleep fijo
        # corto) antes de refrescar la página — si el rerun cae antes de que
        # exista "corriendo": true, esta página no entra al loop de
        # auto-refresco de más abajo y se queda mostrando la tabla vieja
        # hasta que el usuario interactúa de nuevo a mano.
        for _ in range(20):
            estado_nuevo = leer_estado_scan()
            if estado_nuevo and estado_nuevo.get("corriendo"):
                break
            time.sleep(0.25)
        st.rerun()

if click_detener:
    almacen.escribir(DETENER_FILE, b"")
    st.warning("Deteniendo... termina la request en curso y corta ahí.")

if proceso_muerto:
    p = estado_previo["progreso"]
    st.error(
        f"El scan se cortó de forma inesperada (no fue el botón Detener) — "
        f"se quedó en [{p.get('sitio', '?')}] {p.get('etiqueta', '')} "
        f"({p.get('i', '?')}/{p.get('total', '?')}). "
        f"Las ofertas que ya había encontrado en este escaneo se guardaron igual."
    )
    if JOB_NAME:
        st.caption("El detalle del fallo está en los logs del servidor (el administrador recibe la alerta).")
    elif os.path.exists(SCAN_LOG_FILE):
        with open(SCAN_LOG_FILE, encoding="utf-8", errors="replace") as f:
            contenido_log = f.read()
        # Este log llega a la pantalla del usuario: sin nombres del bucket.
        if STORAGE_BASE and STORAGE_BASE.startswith("gs://"):
            nombre_bucket = STORAGE_BASE[len("gs://"):].split("/")[0]
            contenido_log = contenido_log.replace(nombre_bucket, "<bucket>")
        with st.expander("Ver log del proceso (stdout/stderr)", expanded=True):
            st.code(contenido_log[-8000:] or "(log vacío)", language="text")
    else:
        st.caption("No hay scan_stdout.log — este scan se lanzó antes de que se agregara este log (versión vieja de app/gui.py).")

MAX_LINEAS_LOG_VISIBLES = 10

if scan_corriendo:
    p = estado_previo["progreso"]
    ofertas_en_curso = estado_previo.get("ofertas_encontradas", [])

    if p["total"]:
        texto_barra = f"[{p['sitio']}] {p['etiqueta']} ({p['i']}/{p['total']})"
        st.progress(min(p["i"] / p["total"], 1.0), text=texto_barra)
    else:
        st.progress(0.0, text=p["sitio"] or "Iniciando...")

    # Barra aparte con el avance de TODO el proceso (todos los sitios que
    # corren en paralelo por round robin combinados), no solo el sitio que
    # justo escribió el último paso. El total sube a medida que cada sitio
    # va revelando el suyo (ej. GetOnBrd pasa de 6 categorías a ~350+
    # ofertas individuales una vez termina de recorrerlas) — es una
    # estimación que se va afinando, no un total fijo desde el arranque.
    pasos_completados = estado_previo.get("pasos_completados", 0)
    pasos_totales = sum(estado_previo.get("pasos_totales_por_sitio", {}).values())
    if pasos_totales:
        st.progress(min(pasos_completados / pasos_totales, 1.0), text=f"Proceso general ({pasos_completados}/{pasos_totales})")
    else:
        st.progress(0.0, text="Proceso general — iniciando...")

    if estado_previo.get("inicio_ts"):
        st.caption(f"⏱ Tiempo transcurrido: {scanner.formatear_duracion(time.time() - estado_previo['inicio_ts'])}")

    log = estado_previo.get("log", [])
    with st.expander(f"Ver log (últimas {min(len(log), MAX_LINEAS_LOG_VISIBLES)} de {len(log)} líneas)", expanded=False, key="log_expander"):
        st.code("\n".join(log[-MAX_LINEAS_LOG_VISIBLES:]) or "(sin actividad todavía)", language="text")

    if ofertas_en_curso:
        st.subheader(f"Ofertas encontradas en este escaneo ({len(ofertas_en_curso)})")
        st.dataframe(pd.DataFrame(ofertas_en_curso), width="stretch", height=300)
    else:
        st.caption("Todavía no encontró ninguna oferta que pase los filtros en este escaneo.")

    time.sleep(1)
    st.rerun()
elif estado_previo and estado_previo.get("resultado"):
    r = estado_previo["resultado"]
    if r["detenido"]:
        st.warning(f"Detenido — {r['nuevas']} ofertas nuevas guardadas antes de parar. {r['activas']}/{r['total']} activas y relevantes en total.")
    else:
        st.success(f"Listo — {r['nuevas']} ofertas nuevas de este escaneo. {r['activas']}/{r['total']} activas y relevantes en total.")
    if r.get("duracion_seg") is not None:
        st.caption(f"⏱ Duración del último escaneo: {scanner.formatear_duracion(r['duracion_seg'])}")
    correo = estado_previo.get("correo")
    if correo and correo["enviado"]:
        st.caption("✉ Correo de aviso enviado.")
    else:
        if correo:
            st.warning(f"No se pudo enviar el correo de aviso: {correo['error']}")
        _ofrecer_envio_manual(estado_previo)
    errores = estado_previo.get("errores", [])
    if errores:
        with st.expander(f"⚠ Hubo {len(errores)} error(es) durante el scan"):
            st.code("\n".join(errores), language="text")

# ---------------------------------------------------------------------------
# Tabla de resultados
# ---------------------------------------------------------------------------

csv_guardado = almacen.leer(scanner.OUTPUT_CSV)
if csv_guardado is not None:
    df = pd.read_csv(io.BytesIO(csv_guardado))

    col_titulo, col_limpiar = st.columns([4, 1])
    with col_titulo:
        st.subheader(f"Resultados guardados ({len(df)} ofertas)")
    with col_limpiar:
        if st.button("🗑️ Limpiar tabla"):
            st.session_state.confirmar_limpiar = True

    if st.session_state.get("confirmar_limpiar"):
        st.warning("¿Seguro que quieres borrar todos los resultados guardados? No se puede deshacer.")
        col_si, col_no = st.columns(2)
        with col_si:
            if st.button("Sí, borrar todo", type="primary"):
                almacen.borrar(scanner.OUTPUT_CSV)
                st.session_state.confirmar_limpiar = False
                st.rerun()
        with col_no:
            if st.button("Cancelar"):
                st.session_state.confirmar_limpiar = False
                st.rerun()

    col1, col2, col3 = st.columns(3)
    with col1:
        filtro_sitio = st.multiselect("Filtrar por sitio", sorted(df["sitio"].unique()))
    with col2:
        filtro_remota = st.selectbox("Remota", ["Todas", "SI", "NO", "?"])
    with col3:
        filtro_texto = st.text_input("Buscar en título / keyword")

    df_filtrado = df.copy()
    if filtro_sitio:
        df_filtrado = df_filtrado[df_filtrado["sitio"].isin(filtro_sitio)]
    if filtro_remota != "Todas":
        df_filtrado = df_filtrado[df_filtrado["remota"] == filtro_remota]
    if filtro_texto:
        mask = (
            df_filtrado["titulo"].str.contains(filtro_texto, case=False, na=False)
            | df_filtrado["keywords_match"].str.contains(filtro_texto, case=False, na=False)
        )
        df_filtrado = df_filtrado[mask]

    st.dataframe(df_filtrado, width="stretch", height=400)
    st.download_button(
        "Descargar CSV filtrado",
        df_filtrado.to_csv(index=False).encode("utf-8"),
        f"ofertas_filtradas_{datetime.now():%Y-%m-%d}.csv",
        "text/csv",
    )

    # -----------------------------------------------------------------
    # Extraer descripción completa de una oferta
    # -----------------------------------------------------------------
    st.subheader("Extraer descripción completa")

    opciones_link = [""] + df_filtrado["link"].dropna().tolist()
    link_elegido = st.selectbox("Elegir de la tabla filtrada", opciones_link)
    link_manual = st.text_input("...o pegar un link directamente")
    link_final = (link_manual.strip() or link_elegido).strip()

    if link_final and st.button("Extraer texto"):
        dominio = link_final.replace("www.", "").replace("cl.", "")
        extractor_fn = None
        for clave, fn in extractor.EXTRACTORES.items():
            if clave in dominio:
                extractor_fn = fn
                break

        if extractor_fn is None:
            st.warning("Sitio sin extracción automática (Laborum / Trabajando.cl) — copia el texto directo desde el navegador.")
        else:
            with st.spinner("Descargando..."):
                try:
                    resp = requests.get(link_final, headers=extractor.HEADERS, timeout=15)
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, "html.parser")
                    titulo_el = soup.select_one("h1")
                    titulo = titulo_el.get_text(strip=True) if titulo_el else "(sin título detectado)"
                    descripcion = extractor_fn(soup) or "(no se pudo extraer la descripción)"
                    texto_final = f"TÍTULO: {titulo}\nLINK: {link_final}\n\n{descripcion}"
                    st.text_area("Texto extraído (copiar y pegar en el Project de Claude)", value=texto_final, height=400)
                except requests.RequestException as e:
                    st.error(f"Error al descargar: {e}")
else:
    st.info("Todavía no hay resultados guardados — ejecuta un scan primero.")
