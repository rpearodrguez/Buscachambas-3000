"""
GUI (Streamlit) del Job Scanner
--------------------------------
Interfaz web local para correr scan_getonbrd.py y extraer_oferta.py sin
tocar la línea de comandos: elegir país/sitios/keywords, correr el scan
con barra de progreso, filtrar y exportar resultados, y extraer el texto
completo de una oferta puntual.

El scan corre como un PROCESO APARTE del sistema operativo (no un hilo
dentro de Streamlit): esta página lo lanza con subprocess.Popen y desde
ahí solo lee su progreso desde un archivo JSON (scan_status.json) que el
propio scan_getonbrd.py va escribiendo. Esto es a propósito — así el scan
sigue corriendo aunque cierres el navegador, reinicies Streamlit, o esté
corriendo mientras se edita el código: no depende de objetos en memoria
de esta página, que se pierden en cualquiera de esos casos.

USO:
    pip install streamlit pandas pypdf python-docx pyperclip
    streamlit run gui.py
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

import almacen as almacenamiento
import lanzador
import scan_getonbrd as scanner
import extraer_oferta as extractor

STATUS_FILE = "scan_status.json"
DETENER_FILE = "detener.flag"
KEYWORDS_GUI_FILE = "keywords_gui_actual.txt"
SCAN_LOG_FILE = "scan_stdout.log"

# Dónde viven los archivos compartidos con el scan (estado, flag de detener,
# CSV, keywords). Sin la variable de entorno, archivos en disco como siempre;
# con BUSCACHAMBAS_STORAGE=gs://bucket, en la carpeta de este usuario del
# bucket. USUARIO_LOCAL es un reemplazo hasta que haya login por usuario.
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
        return None
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_info(info)


STORAGE_BASE = _config("BUSCACHAMBAS_STORAGE")
STORAGE_DESTINO = almacenamiento.destino_de_usuario(STORAGE_BASE, USUARIO_LOCAL)
# Si está definido, el scan corre como Cloud Run Job (projects/../jobs/..) en
# vez de un subproceso local; requiere STORAGE_BASE para compartir el estado.
JOB_NAME = _config("BUSCACHAMBAS_JOB")

st.set_page_config(page_title="Job Scanner", layout="wide")
st.title("Job Scanner")


# Cacheado: crear el almacén de un bucket verifica que sea privado (una
# consulta a la API), y esta página se re-ejecuta entera cada segundo
# mientras corre un scan.
@st.cache_resource
def _crear_almacen(destino):
    return almacenamiento.crear_almacen(destino, credenciales=_credenciales_gcp())


if JOB_NAME and not STORAGE_DESTINO:
    st.error("Configuración incompleta: el escaneo en la nube necesita almacenamiento compartido.")
    st.stop()

try:
    almacen = _crear_almacen(STORAGE_DESTINO)
except almacenamiento.ErrorAlmacen as e:
    st.error(str(e))
    st.stop()


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

    cmd = [sys.executable, "scan_getonbrd.py"] + argumentos

    # stdout/stderr a un archivo (no DEVNULL): si el proceso crashea con una
    # excepción no manejada, scan_status.json queda con "corriendo": true
    # para siempre (nunca llega a escribirse el resultado final) y sin este
    # log no hay forma de saber por qué murió.
    log = open(SCAN_LOG_FILE, "w", encoding="utf-8")
    subprocess.Popen(
        cmd,
        cwd=os.path.dirname(os.path.abspath(__file__)),
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

st.sidebar.header("Configuración")

with st.sidebar.expander("País y sitios", expanded=True):
    pais = st.selectbox("País", list(scanner.PAISES.keys()))
    ids_disponibles = scanner.PAISES[pais]
    sitios_elegidos = st.multiselect(
        "Sitios a escanear",
        options=ids_disponibles,
        default=ids_disponibles,
        format_func=lambda i: scanner.SITIOS_POR_ID[i]["nombre"],
    )
    require_remote = st.checkbox("Solo ofertas remotas", value=scanner.REQUIRE_REMOTE)

with st.sidebar.expander("Aviso por correo"):
    if JOB_NAME:
        # En la nube el SMTP (cuenta y contraseña) lo tiene configurado el
        # job, no se escribe ni se guarda desde esta pantalla: acá solo se
        # elige a quién avisar.
        destino_aviso = st.text_input("Correo que recibirá el aviso", key="email_destino_nube").strip()
        config_email = {"destinatario": destino_aviso}
        email_listo = "@" in destino_aviso
        enviar_correo = st.checkbox("Enviar correo al terminar el scan", value=False, disabled=not email_listo)
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
    keywords_default = "\n".join(scanner.cargar_keywords(almacen=almacen))
    keywords_text = st.text_area("Una por línea", value=keywords_default, height=180)
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
        try:
            lanzar_scan(
                pais, sitios_elegidos, keywords, require_remote, click_buscar_completa,
                email_a=config_email["destinatario"] if enviar_correo and email_listo else None,
            )
        except (lanzador.ErrorLanzador, almacenamiento.ErrorAlmacen) as e:
            st.error(str(e))
            st.stop()
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
        st.caption("No hay scan_stdout.log — este scan se lanzó antes de que se agregara este log (versión vieja de gui.py).")

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
    if correo:
        if correo["enviado"]:
            st.caption("✉ Correo de aviso enviado.")
        else:
            st.warning(f"No se pudo enviar el correo de aviso: {correo['error']}")
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
