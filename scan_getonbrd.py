"""
Job Scanner multi-sitio / multi-país (GetOnBrd, Computrabajo, Laborum,
Trabajando.cl, LinkedIn)
-----------------------------------------------------------
Recorre los sitios de empleo configurados para un país, se queda con
las ofertas remotas que matchean algún keyword, y exporta un CSV único
con: sitio, título, empresa, keywords que matchearon, si es remota, si
sigue activa, y el link.

USO:
    pip install requests beautifulsoup4 --break-system-packages

    python scan_getonbrd.py                        # país por defecto (Chile), todos sus sitios
    python scan_getonbrd.py --pais Chile
    python scan_getonbrd.py --pais Chile --solo laborum_cl      # solo re-correr un sitio puntual
    python scan_getonbrd.py --keywords-file keywords.txt        # usar keywords desde archivo
    python scan_getonbrd.py --generar-prompt-keywords            # imprime un prompt para pegar en Claude
                                                                   # y generar keywords.txt, no escanea nada
    python scan_getonbrd.py --descripcion-completa                # más lento: visita cada oferta de
                                                                   # Computrabajo/ChileTrabajos/LinkedIn/Magneto
                                                                   # para sacar su descripción real

CONFIGURACIÓN:
    - keywords.txt (opcional, una keyword por línea): si existe, se usa
      en vez de DEFAULT_KEYWORDS. Se puede generar su contenido pegando
      en Claude el prompt de --generar-prompt-keywords.
    - PAISES / SITIOS más abajo: agregar un país nuevo es agregar sus
      adaptadores a SITIOS y una entrada en PAISES con sus ids.
    - Correr sin --solo no borra resultados de otros sitios/países ya
      guardados en el CSV: solo se reemplazan las filas de los sitios
      que efectivamente corrieron esta vez.

NOTAS POR SITIO (Chile):
    - GetOnBrd: no tiene buscador por texto libre (/empleos/busqueda
      devuelve 404). Se recorren categorías (/jobs/<categoria>) y se
      visita cada oferta para revisar si sigue activa, si es remota, y
      si el texto matchea algún keyword.
    - Computrabajo (cl.computrabajo.com): sí soporta búsqueda por texto
      vía /trabajo-de-<keyword>. La modalidad (remoto/híbrido/presencial)
      viene en la misma tarjeta de resultados, sin visitar cada oferta.
    - Laborum (laborum.cl): es una SPA — la búsqueda real ocurre en
      POST /api/avisos/searchV2 con header "x-site-id: BMCL" y body
      {"query": "<keyword>"}. La modalidad también viene en la respuesta
      (campo "modalidadTrabajo"), así que tampoco hace falta visitar
      cada oferta. El link de la oferta se arma como
      /empleos/<slug-del-titulo>-<id>.html; el id en la URL es lo que
      realmente usa el sitio para resolver la oferta al abrirla.
    - Trabajando.cl: también es una SPA — la búsqueda real ocurre en
      GET /api/searchjob?palabraClave=<keyword>&pagina=1&orden=RANKING&
      tipoOrden=DESC (sin ubicacion/region busca en todo Chile). La
      modalidad viene en "nombreJornada": "Teletrabajo" es 100% remoto,
      "Mixta (Teletrabajo + Presencial)" es híbrido (no cuenta como
      remoto acá). El link se arma como
      /trabajo-empleo/<keyword>/trabajo/<idOferta>-<slug-del-cargo>.
    - LinkedIn: no tiene RSS oficial. Se usa el endpoint público
      "jobs-guest" (sin login) que LinkedIn usa internamente para su
      propio buscador. No es una API oficial/documentada: puede cambiar
      o dejar de funcionar sin aviso. Se filtra a remoto vía f_WT=2.

    En los 4 sitios de búsqueda por keyword (Computrabajo, Laborum,
    Trabajando.cl, LinkedIn) no se verifica "activa" visitando cada
    oferta: sus buscadores son índices en vivo, así que lo que
    devuelven ya está activo por definición.
"""

import os
import time
import csv
import sys
import json
import re
import argparse
import html
import io
import smtplib
import unicodedata
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

from almacen import Almacen, AlmacenLocal, ErrorAlmacen, crear_almacen
from extraer_oferta import EXTRACTORES as _EXTRACTORES_DETALLE
from extraer_oferta import extraer_id_trabajando, extraer_trabajando

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Keywords por defecto si no existe keywords.txt (ver cargar_keywords()).
# Stack de ejemplo (automatización, backup, soporte técnico avanzado) —
# pensadas para Chile. Para otro perfil/país, generar keywords.txt con
# --generar-prompt-keywords.
DEFAULT_KEYWORDS = [
    "automatización",
    "python",
    "selenium",
    "platform engineer",
    "soporte técnico",
    "backup",
    "data protection",
    "tier 3",
    "powershell",
]

KEYWORDS_FILE_DEFAULT = "keywords.txt"
PERFIL_FILE_DEFAULT = "perfil.txt"

REQUIRE_REMOTE = True

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

REQUEST_DELAY_SECONDS = 1.5  # delay entre requests, para no saturar los sitios
OUTPUT_CSV = "ofertas_empleos.csv"

# Con --storage gs://..., cada subida del estado es una escritura al bucket
# (5.000 gratis al mes en total, y un scan completo da ~1.600 pasos), y cada
# chequeo del flag de detener una lectura. Se espacian; ver EscritorEstado.
INTERVALO_GUARDADO_NUBE = 10
INTERVALO_DETENER_NUBE = 3
CSV_FIELDS = ["sitio", "titulo", "empresa", "keywords_match", "remota", "activa", "motivo", "link", "fecha_creacion", "salario", "descripcion"]
# Nota: "salario" y "descripcion" quedan vacíos para los sitios que no los
# expongan — csv.DictWriter completa con "" los campos que falten en una
# fila, no hace falta que todos los sitios lo llenen. Ver README para el
# detalle de qué nivel de descripción trae cada sitio (completa, snippet
# corto, o nada salvo que se corra con --descripcion-completa).


def hoy() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def formatear_duracion(segundos: float) -> str:
    segundos = int(round(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    if horas:
        return f"{horas} h {minutos:02d} min {segs:02d} s"
    if minutos:
        return f"{minutos} min {segs:02d} s"
    return f"{segs} s"


# Credenciales SMTP para el aviso por correo al terminar el scan. Vive en un
# archivo aparte (gitignored) porque tiene la contraseña; ver
# email_config.example.json para el formato.
EMAIL_CONFIG_FILE = "email_config.json"
MAX_OFERTAS_EN_CORREO = 100


def cargar_config_email() -> dict | None:
    """SMTP desde variables de entorno (así lo recibe el Cloud Run Job, con
    la contraseña inyectada desde Secret Manager) o, si no están, desde
    EMAIL_CONFIG_FILE (uso local)."""
    host = os.environ.get("BUSCACHAMBAS_SMTP_HOST")
    if host:
        return {
            "host": host,
            "port": int(os.environ.get("BUSCACHAMBAS_SMTP_PORT", "587")),
            "usuario": os.environ.get("BUSCACHAMBAS_SMTP_USER", ""),
            "password": os.environ.get("BUSCACHAMBAS_SMTP_PASSWORD", ""),
            "remitente": os.environ.get("BUSCACHAMBAS_SMTP_FROM", ""),
        }
    if not os.path.exists(EMAIL_CONFIG_FILE):
        return None
    try:
        with open(EMAIL_CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def guardar_config_email(config: dict):
    with open(EMAIL_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def config_email_completa(config: dict | None) -> bool:
    return bool(config) and all(config.get(k) for k in ("host", "port", "usuario", "password", "destinatario"))


def enviar_correo_fin(destinatario: str, asunto: str, cuerpo: str, adjuntos: list[tuple[str, bytes]] | None = None) -> str | None:
    """Manda el aviso de fin de scan, con `adjuntos` como pares (nombre de
    archivo, contenido). Devuelve None si salió bien, o el mensaje de error
    — nunca lanza: un correo que falla no debe tumbar un scan que ya
    terminó y guardó sus resultados."""
    config = cargar_config_email()
    if not config:
        return f"no hay {EMAIL_CONFIG_FILE} (o está mal formado) — copia email_config.example.json y complétalo"
    try:
        msg = EmailMessage()
        msg["Subject"] = asunto
        msg["From"] = config.get("remitente") or config["usuario"]
        msg["To"] = destinatario
        msg.set_content(cuerpo)

        for nombre_archivo, datos in adjuntos or []:
            es_csv = nombre_archivo.lower().endswith(".csv")
            # BOM para que Excel abra el CSV en UTF-8 y no rompa las tildes.
            if es_csv and not datos.startswith(b"\xef\xbb\xbf"):
                datos = b"\xef\xbb\xbf" + datos
            msg.add_attachment(datos, maintype="text", subtype="csv" if es_csv else "plain", filename=nombre_archivo)

        puerto = int(config.get("port", 587))
        if puerto == 465:
            servidor = smtplib.SMTP_SSL(config["host"], puerto, timeout=30)
        else:
            servidor = smtplib.SMTP(config["host"], puerto, timeout=30)
        with servidor:
            if puerto != 465:
                servidor.starttls()
            servidor.login(config["usuario"], config["password"])
            servidor.send_message(msg)
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def armar_correo_fin(pais: str, filas: list[dict], filas_finales: list[dict], duracion: float,
                     detenido: bool, errores: list[str]) -> tuple[str, str]:
    activas = len([f for f in filas_finales if f["activa"] == "SI"])
    estado = "detenido" if detenido else "terminado"
    asunto = f"Job Scanner {estado}: {len(filas)} ofertas nuevas ({pais})"

    lineas = [
        f"Scan {estado} en {formatear_duracion(duracion)}.",
        f"{len(filas)} ofertas nuevas de este escaneo; {activas}/{len(filas_finales)} activas y relevantes en total.",
    ]
    if errores:
        lineas.append(f"{len(errores)} error(es) durante el scan (ver el log en la GUI).")
    if filas:
        lineas += ["", "Ofertas nuevas:"]
        for f in filas[:MAX_OFERTAS_EN_CORREO]:
            lineas.append(f"- [{f['sitio']}] {f['titulo']} ({f['empresa']})\n  {f['link']}")
        if len(filas) > MAX_OFERTAS_EN_CORREO:
            lineas.append(f"... y {len(filas) - MAX_OFERTAS_EN_CORREO} más en el CSV adjunto.")
    lineas += ["", f"Adjunto: {OUTPUT_CSV} con todos los resultados guardados."]
    return asunto, "\n".join(lineas)


def _parsear_fecha(fecha_str: str, formato: str) -> str | None:
    """Convierte una fecha de un sitio (ej. '05-07-2026') a 'YYYY-MM-DD'
    para que quede consistente en el CSV. Devuelve None si no se pudo
    parsear, para que el llamador pueda caer de vuelta a hoy()."""
    try:
        return datetime.strptime(fecha_str.strip(), formato).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def slugify_ascii(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "-", texto).strip("-").lower()
    return texto


def _quitar_tags_html(texto: str) -> str:
    return re.sub(r"<[^>]+>", "", texto).strip()


def _obtener_descripcion_detalle(link: str) -> str:
    """Visita la página de detalle de una oferta y extrae su descripción
    completa, reusando los mismos selectores que extraer_oferta.py (para
    no duplicar el mapeo de sitio → selector en dos archivos). Solo se usa
    cuando el modo --descripcion-completa está activo, en sitios cuyo
    buscador no trae ya la descripción en la respuesta de búsqueda."""
    dominio = urlparse(link).netloc.replace("www.", "").replace("cl.", "")

    if "trabajando.cl" in dominio:
        # Caso especial: no es un selector sobre HTML sino un fetch a la
        # API de detalle por id (ver extraer_trabajando en extraer_oferta.py).
        id_oferta = extraer_id_trabajando(link)
        if not id_oferta:
            return ""
        try:
            _, descripcion = extraer_trabajando(id_oferta)
            return descripcion
        except requests.RequestException:
            return ""

    extractor = next((fn for clave, fn in _EXTRACTORES_DETALLE.items() if clave in dominio), None)
    if extractor is None:
        return ""
    try:
        resp = requests.get(link, headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except requests.RequestException:
        return ""
    return extractor(BeautifulSoup(resp.text, "html.parser"))


def _completar_descripciones(aceptadas: dict, on_progreso, debe_detener):
    """Fase 2 opcional (--descripcion-completa): visita cada oferta que ya
    pasó los filtros de keyword/remoto y le agrega la descripción completa.
    Generador para poder usarse con "yield from" dentro de otro generador
    y mantener el mismo round robin por request que el resto del scan."""
    total = len(aceptadas)
    for i, (link, data) in enumerate(aceptadas.items(), 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"descripción: {data['titulo'][:40]}", i, total)
        # Si falla o no hay extractor para el dominio, se deja lo que ya
        # había (snippet corto o vacío) en vez de pisarlo con "".
        descripcion = _obtener_descripcion_detalle(link)
        if descripcion:
            data["descripcion"] = descripcion
        yield


def cargar_keywords(path: str = KEYWORDS_FILE_DEFAULT, almacen: Almacen | None = None) -> list[str]:
    """Lee keywords desde un archivo (una por línea, '#' comenta la línea).
    Si no existe o queda vacío, usa DEFAULT_KEYWORDS."""
    texto = (almacen or AlmacenLocal()).leer_texto(path)
    if texto is not None:
        kws = [linea.strip() for linea in texto.splitlines() if linea.strip() and not linea.strip().startswith("#")]
        if kws:
            return kws
    return DEFAULT_KEYWORDS


def construir_prompt_keywords(perfil: str = "") -> str:
    """Arma el texto del prompt para pedirle keywords a Claude. Separado de
    generar_prompt_keywords() para poder reusarlo desde la GUI (Streamlit)
    sin depender de un archivo en disco ni de print()."""
    bloque_perfil = perfil.strip() if perfil.strip() else "[Pega acá tu perfil/CV o una descripción de tu stack técnico y objetivo laboral]"

    return f"""Actúa como reclutador técnico senior especializado en el mercado laboral tech.

Perfil del candidato:
{bloque_perfil}

Necesito una lista de keywords de búsqueda para portales de empleo (GetOnBrd, Computrabajo, Laborum, LinkedIn, etc.), no términos genéricos de CV.

Instrucciones:
- Devuelve entre 8 y 15 keywords o frases cortas (máximo 2-3 palabras) que realmente aparecen en avisos de trabajo reales para este perfil.
- Prioriza términos técnicos concretos (herramientas, lenguajes, certificaciones, nombres de rol) por sobre términos genéricos.
- Mezcla español e inglés según cómo se busca habitualmente cada término (ej. "soporte técnico" pero "platform engineer").
- Responde SOLO con la lista, una keyword por línea, sin numeración, viñetas ni texto adicional — la voy a pegar directo en un archivo keywords.txt."""


def generar_prompt_keywords(perfil_path: str = PERFIL_FILE_DEFAULT) -> None:
    """Imprime (CLI) el prompt para pegar en Claude y generar keywords.txt.
    No escanea nada — es solo texto para copiar/pegar."""
    perfil = ""
    if os.path.exists(perfil_path):
        with open(perfil_path, encoding="utf-8") as f:
            perfil = f.read().strip()

    print(construir_prompt_keywords(perfil))
    print(f"\n---\nGuarda la respuesta de Claude en '{KEYWORDS_FILE_DEFAULT}' (una keyword por línea) y vuelve a correr el script.")


# ---------------------------------------------------------------------------
# GetOnBrd
# ---------------------------------------------------------------------------

GETONBRD_BASE_URL = "https://www.getonbrd.com"

# Categorías a recorrer (slugs reales de /jobs/<slug>). Lista completa de
# categorías disponible navegando https://www.getonbrd.com/empleos/
GETONBRD_CATEGORIES = [
    "programming",
    "sysadmin-devops-qa",
    "technical-support",
    "cybersecurity",
    "machine-learning-ai",
    "data-science-analytics",
]

GETONBRD_SEÑALES_CERRADA = [
    "closed job",
    "ya no acepta postulaciones",
    "no longer accepting applications",
    "esta oferta ha sido cerrada",
    "oferta cerrada",
    "position filled",
    "posición ya fue cubierta",
    "ya no está disponible",
]

GETONBRD_SEÑALES_REMOTO = [
    "fully remote",
    "100% remoto",
    "100% remote",
    "remote-first",
    "trabajo remoto",
]

# GetOnBrd mezcla mes en inglés y en español según la categoría/oferta (ej.
# "Jul 16" o "jul 16", o a veces "ago"/"dic" en vez de "aug"/"dec") y nunca
# da el año — se asume el año actual, corrigiendo si eso da una fecha
# futura (ej. ver "dic 20" en enero, que en realidad es del año pasado).
GETONBRD_MESES = {
    "ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5,
    "jun": 6, "jul": 7, "ago": 8, "aug": 8, "sep": 9, "oct": 10, "nov": 11,
    "dic": 12, "dec": 12,
}


def _getonbrd_parsear_fecha(texto: str) -> str | None:
    m = re.match(r"([A-Za-zñÑ]{3,4})\.?\s+(\d{1,2})", (texto or "").strip())
    if not m:
        return None
    mes = GETONBRD_MESES.get(m.group(1).lower()[:3])
    if not mes:
        return None
    ahora = datetime.now()
    try:
        fecha = datetime(ahora.year, mes, int(m.group(2)))
    except ValueError:
        return None
    if fecha > ahora:
        fecha = fecha.replace(year=ahora.year - 1)
    return fecha.strftime("%Y-%m-%d")

# GetOnBrd no filtra por país en el servidor (confirmado: ?country=<x> no
# cambia los resultados) — el listado es un pool único para toda
# Latinoamérica. Para poder tener una versión "por país" hay que leer el
# texto completo de cada oferta (que ya se descarga para el keyword-match)
# y buscar ahí el nombre del país o de sus ciudades principales. Una
# oferta también cuenta como válida para cualquier país si dice
# explícitamente que está abierta a toda Latinoamérica.
GETONBRD_CIUDADES_POR_PAIS = {
    "Chile": ["chile", "santiago", "valparaíso", "valparaiso", "concepción", "concepcion",
              "antofagasta", "temuco", "viña del mar", "vina del mar", "la serena"],
    "Colombia": ["colombia", "bogotá", "bogota", "medellín", "medellin", "cali",
                 "barranquilla", "cartagena", "bucaramanga", "pereira"],
}
GETONBRD_SEÑALES_LATAM_ABIERTO = [
    "latinoamérica", "latinoamerica", "latam", "cualquier país de", "cualquier pais de",
]
# Todas las ciudades/países que reconocemos, juntas — para el caso "no
# menciona ninguna ciudad/país conocido" (ej. la tarjeta solo dice
# "Remoto" a secas, sin especificar dónde): se trata como abierta a
# cualquier país en vez de excluirla, porque no hay ninguna señal de que
# esté restringida a un país puntual.
GETONBRD_TODAS_LAS_CIUDADES = {c for lista in GETONBRD_CIUDADES_POR_PAIS.values() for c in lista}


def _es_link_de_oferta_getonbrd(href: str) -> bool:
    """Distingue un link de oferta puntual (/jobs/<categoria>/<slug>) de un
    link de navegación a la categoría misma (/jobs/<categoria>)."""
    partes = [p for p in urlparse(href).path.split("/") if p]
    return len(partes) >= 3 and partes[0] in ("jobs", "empleos")


def _getonbrd_ofertas_categoria(categoria: str) -> list[dict]:
    url = f"{GETONBRD_BASE_URL}/jobs/{categoria}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    ofertas = []

    # GetOnBrd lista cada oferta como <a> hacia /jobs/... o /empleos/...
    # (usa ambos esquemas de URL indistintamente para las mismas ofertas).
    for card in soup.select("a[href*='/empleos/'], a[href*='/jobs/']"):
        href = card.get("href", "")
        if not href or not _es_link_de_oferta_getonbrd(href):
            continue

        # La tarjeta trae título/empresa/fecha/sueldo en elementos propios
        # (h4.results-list-title, etc.). Si no aparece ese selector, el link
        # no es una tarjeta de oferta real sino algo como un filtro de la
        # barra lateral (ciudad/skill, ej. "Santiago (460)") que también
        # matchea /jobs/ o /empleos/ con ≥3 segmentos — se descarta en vez
        # de usar el texto del link como título (antes inflaba el conteo
        # con filtros disfrazados de ofertas).
        titulo_el = card.select_one("h4.results-list-title strong")
        if not titulo_el:
            continue
        titulo = titulo_el.get_text(strip=True)
        if not titulo:
            continue
        link = urljoin(GETONBRD_BASE_URL, href)

        empresa_el = card.select_one("div.results-list-info div.size0 strong")
        empresa = empresa_el.get_text(strip=True) if empresa_el else ""

        fecha_el = card.select_one("div.results-secondary.hide-on-mobile div.opacity-half.size0")
        fecha = _getonbrd_parsear_fecha(fecha_el.get_text(strip=True)) if fecha_el else None

        salario = ""
        icono_salario = card.select_one("i.icon-money-bill")
        if icono_salario:
            span_salario = icono_salario.find_next_sibling("span")
            if span_salario:
                salario = span_salario.get_text(strip=True)

        ofertas.append({
            "titulo": titulo, "link": link, "empresa": empresa,
            "fecha": fecha, "salario": salario,
        })

    return ofertas


def _getonbrd_extraer_descripcion(html_crudo: str) -> str:
    soup = BeautifulSoup(html_crudo, "html.parser")
    body = soup.select_one("div#job-body")
    return body.get_text("\n", strip=True) if body else ""


def _getonbrd_inspeccionar_oferta(link: str) -> dict:
    try:
        resp = requests.get(link, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"activa": False, "motivo": f"error de red: {e}", "remota": False, "texto": "", "descripcion": ""}

    texto = resp.text.lower()

    for señal in GETONBRD_SEÑALES_CERRADA:
        if señal in texto:
            return {"activa": False, "motivo": f"cerrada (detectado: '{señal}')", "remota": False, "texto": texto, "descripcion": ""}

    remota = any(señal in texto for señal in GETONBRD_SEÑALES_REMOTO)
    descripcion = _getonbrd_extraer_descripcion(resp.text)
    return {"activa": True, "motivo": "activa", "remota": remota, "texto": texto, "descripcion": descripcion}


def escanear_getonbrd(keywords: list[str], require_remote: bool, location: str = "Chile", nombre_sitio: str = "GetOnBrd", on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
    candidatas = {}
    total_categorias = len(GETONBRD_CATEGORIES)
    for i, categoria in enumerate(GETONBRD_CATEGORIES, 1):
        if debe_detener and debe_detener():
            return []
        if on_progreso:
            on_progreso(f"categoría: {categoria}", i, total_categorias)
        try:
            print(f"  → GetOnBrd / categoría: {categoria}")
            for oferta in _getonbrd_ofertas_categoria(categoria):
                oferta["categoria_origen"] = categoria
                candidatas.setdefault(oferta["link"], oferta)
        except requests.RequestException as e:
            mensaje = f"GetOnBrd, categoría '{categoria}': {e}"
            print(f"    ⚠ error en categoría '{categoria}': {e}", file=sys.stderr)
            if on_error:
                on_error(mensaje)
        yield

    # Intercalar la revisión por categoría (una oferta de cada categoría
    # por turno) en vez de agotar una categoría entera antes de pasar a la
    # siguiente. No reduce la carga total a GetOnBrd (siguen siendo las
    # mismas requests), pero si el scan se corta a mitad de camino deja
    # cobertura pareja en todas las categorías en vez de completa en
    # algunas y nula en el resto.
    por_categoria: dict[str, list[tuple[str, dict]]] = {}
    for link, data in candidatas.items():
        por_categoria.setdefault(data.get("categoria_origen", ""), []).append((link, data))

    orden_intercalado = []
    colas = [cola for cola in por_categoria.values() if cola]
    while colas:
        siguientes_colas = []
        for cola in colas:
            orden_intercalado.append(cola.pop(0))
            if cola:
                siguientes_colas.append(cola)
        colas = siguientes_colas

    filas = []
    total_ofertas = len(orden_intercalado)
    for i, (link, data) in enumerate(orden_intercalado, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"revisando: {data['titulo'][:40]}", i, total_ofertas)

        info = _getonbrd_inspeccionar_oferta(link)
        yield

        matches = [kw for kw in keywords if kw.lower() in info["texto"]]
        if not matches:
            continue
        if require_remote and not info["remota"]:
            continue

        señales_pais = GETONBRD_CIUDADES_POR_PAIS.get(location, [location.lower()])
        coincide_pais = (
            any(s in info["texto"] for s in señales_pais)
            or any(s in info["texto"] for s in GETONBRD_SEÑALES_LATAM_ABIERTO)
            or not any(c in info["texto"] for c in GETONBRD_TODAS_LAS_CIUDADES)
        )
        if not coincide_pais:
            continue

        fila = {
            "sitio": nombre_sitio,
            "titulo": data["titulo"],
            "empresa": data.get("empresa", ""),
            "keywords_match": ", ".join(matches),
            "remota": "SI" if info["remota"] else "NO",
            "activa": "SI" if info["activa"] else "NO",
            "motivo": info["motivo"],
            "link": link,
            "fecha_creacion": data.get("fecha") or hoy(),
            "salario": data.get("salario", ""),
            "descripcion": info.get("descripcion", ""),
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)

    return filas


# ---------------------------------------------------------------------------
# Computrabajo (misma plataforma en varios países — cambia el subdominio)
# ---------------------------------------------------------------------------

COMPUTRABAJO_BASE_URL = "https://cl.computrabajo.com"  # default, se puede pasar otro por base_url


def escanear_computrabajo(keywords: list[str], require_remote: bool, base_url: str = COMPUTRABAJO_BASE_URL, nombre_sitio: str = "Computrabajo", on_progreso=None, debe_detener=None, on_error=None, on_oferta=None, descripcion_completa: bool = False) -> list[dict]:
    candidatas = {}

    for i, keyword in enumerate(keywords, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"'{keyword}' — {len(candidatas)} ofertas encontradas", i, len(keywords))
        slug = slugify_ascii(keyword)
        url = f"{base_url}/trabajo-de-{slug}"
        try:
            print(f"  → Computrabajo ({base_url}) / keyword: {keyword}")
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select("article.box_offer"):
                title_a = card.select_one("h2.fs18 a.js-o-link")
                if not title_a:
                    continue
                titulo = title_a.get_text(strip=True)
                href = title_a.get("href", "").split("#")[0]
                link = urljoin(base_url, href)

                empresa_a = card.select_one("[offer-grid-article-company-url]")
                empresa = empresa_a.get_text(strip=True) if empresa_a else ""

                modalidad_span = card.select_one("div.fs13.mt15 span.dIB")
                modalidad_texto = modalidad_span.get_text(strip=True) if modalidad_span else ""
                remota = "remoto" in modalidad_texto.lower()

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo, "empresa": empresa, "remota": remota, "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"Computrabajo, keyword '{keyword}': {e}")
        yield

    aceptadas = {link: data for link, data in candidatas.items() if not require_remote or data["remota"]}
    if descripcion_completa:
        yield from _completar_descripciones(aceptadas, on_progreso, debe_detener)

    filas = []
    for link, data in aceptadas.items():
        fila = {
            "sitio": nombre_sitio,
            "titulo": data["titulo"],
            "empresa": data["empresa"],
            "keywords_match": ", ".join(sorted(data["keywords"])),
            "remota": "SI" if data["remota"] else "NO",
            "activa": "SI",
            "motivo": "en resultados de búsqueda (activa)",
            "link": link,
            "fecha_creacion": hoy(),
            "descripcion": data.get("descripcion", ""),
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# Laborum
# ---------------------------------------------------------------------------

LABORUM_SEARCH_URL = "https://www.laborum.cl/api/avisos/searchV2"
LABORUM_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "x-site-id": "BMCL",
    "Origin": "https://www.laborum.cl",
    "Referer": "https://www.laborum.cl/empleos.html",
}


def escanear_laborum(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
    candidatas = {}

    for i, keyword in enumerate(keywords, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"'{keyword}' — {len(candidatas)} ofertas encontradas", i, len(keywords))
        try:
            print(f"  → Laborum / keyword: {keyword}")
            body = {"query": keyword}
            if require_remote:
                body["filtros"] = [{"id": "modalidad_trabajo", "value": "remoto"}]
            resp = requests.post(
                LABORUM_SEARCH_URL,
                headers=LABORUM_HEADERS,
                params={"pageSize": 50, "page": 0, "sort": "RELEVANTES"},
                data=json.dumps(body),
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("content", []):
                titulo = item.get("titulo", "")
                empresa = item.get("empresa", "") or ""
                modalidad = (item.get("modalidadTrabajo") or "").lower()
                remota = "remoto" in modalidad
                slug = slugify_ascii(f"{titulo} {empresa}")
                link = f"https://www.laborum.cl/empleos/{slug}-{item['id']}.html"
                fecha = _parsear_fecha(item.get("fechaPublicacion", ""), "%d-%m-%Y")

                descripcion = item.get("detalle", "") or ""

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo,
                        "empresa": empresa,
                        "remota": remota,
                        "fecha": fecha,
                        "descripcion": descripcion,
                        "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"Laborum, keyword '{keyword}': {e}")
        yield

    filas = []
    for link, data in candidatas.items():
        if require_remote and not data["remota"]:
            continue
        fila = {
            "sitio": "Laborum",
            "titulo": data["titulo"],
            "empresa": data["empresa"],
            "keywords_match": ", ".join(sorted(data["keywords"])),
            "remota": "SI" if data["remota"] else "NO",
            "activa": "SI",
            "motivo": "en resultados de búsqueda (activa)",
            "link": link,
            "fecha_creacion": data["fecha"] or hoy(),
            "descripcion": data["descripcion"],
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# Trabajando.cl
# ---------------------------------------------------------------------------

TRABAJANDO_SEARCH_URL = "https://www.trabajando.cl/api/searchjob"


def escanear_trabajando(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None, descripcion_completa: bool = False) -> list[dict]:
    candidatas = {}

    for i, keyword in enumerate(keywords, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"'{keyword}' — {len(candidatas)} ofertas encontradas", i, len(keywords))
        try:
            print(f"  → Trabajando.cl / keyword: {keyword}")
            params = {"palabraClave": keyword, "pagina": 1, "orden": "RANKING", "tipoOrden": "DESC"}
            referer = "https://www.trabajando.cl/trabajo-empleo/"
            if require_remote:
                params["jornadas"] = "9"  # 9 = Teletrabajo (100% remoto) en el facet del sitio
                referer += "?jornadas=9"
            resp = requests.get(
                TRABAJANDO_SEARCH_URL,
                headers={**HEADERS, "Referer": referer},
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("ofertas", []):
                titulo = item.get("nombreCargo", "")
                modalidad = (item.get("nombreJornada") or "").strip().lower()
                remota = modalidad == "teletrabajo"
                slug = slugify_ascii(titulo)
                link = f"https://www.trabajando.cl/trabajo/{item['idOferta']}-{slug}"
                # "fechaPublicacion" viene como "2026-07-02 09:33" — ya está
                # en formato YYYY-MM-DD, solo hay que cortar la hora.
                fecha_raw = (item.get("fechaPublicacion") or "").split(" ")[0]
                fecha = fecha_raw if len(fecha_raw) == 10 else None

                # "descripcionOferta" es un snippet corto con el keyword
                # resaltado en <strong> — no es la descripción completa,
                # pero es lo único que trae el buscador sin visitar la
                # oferta, así que se guarda igual (sin las tags).
                descripcion = _quitar_tags_html(item.get("descripcionOferta", "") or "")

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo,
                        "empresa": item.get("nombreEmpresa", "") or "",
                        "remota": remota,
                        "fecha": fecha,
                        "descripcion": descripcion,
                        "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"Trabajando.cl, keyword '{keyword}': {e}")
        yield

    aceptadas = {link: data for link, data in candidatas.items() if not require_remote or data["remota"]}
    if descripcion_completa:
        # Pisa el snippet truncado con la descripción completa de la API
        # de detalle (ver _obtener_descripcion_detalle / extraer_trabajando).
        yield from _completar_descripciones(aceptadas, on_progreso, debe_detener)

    filas = []
    for link, data in aceptadas.items():
        fila = {
            "sitio": "Trabajando.cl",
            "titulo": data["titulo"],
            "empresa": data["empresa"],
            "keywords_match": ", ".join(sorted(data["keywords"])),
            "remota": "SI" if data["remota"] else "NO",
            "activa": "SI",
            "motivo": "en resultados de búsqueda (activa)",
            "link": link,
            "fecha_creacion": data["fecha"] or hoy(),
            "descripcion": data["descripcion"],
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# ChileTrabajos
# ---------------------------------------------------------------------------

CHILETRABAJOS_BASE_URL = "https://www.chiletrabajos.cl"
CHILETRABAJOS_SEÑAL_REMOTO = "completamente desde tu casa"


def escanear_chiletrabajos(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None, descripcion_completa: bool = False) -> list[dict]:
    candidatas = {}

    for i, keyword in enumerate(keywords, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"'{keyword}' — {len(candidatas)} ofertas encontradas", i, len(keywords))
        try:
            print(f"  → ChileTrabajos / keyword: {keyword}")
            resp = requests.get(
                f"{CHILETRABAJOS_BASE_URL}/encuentra-un-empleo",
                headers=HEADERS,
                params={"2": keyword, "action": "search"},
                timeout=15,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select("div.job-item"):
                title_a = card.select_one("h2.title a")
                if not title_a:
                    continue
                titulo = title_a.get_text(strip=True)
                link = urljoin(CHILETRABAJOS_BASE_URL, title_a.get("href", ""))

                meta = card.select_one("h3.meta")
                empresa = meta.get_text(" ", strip=True).split(",")[0].strip() if meta else ""

                remota = any(
                    CHILETRABAJOS_SEÑAL_REMOTO in (icono.get("title") or "")
                    for icono in card.select("a.icon-beneficio")
                )

                # "p.description" trae un resumen truncado (termina en "…"
                # y un link "Ver más" que se saca antes de extraer el texto).
                descripcion_el = card.select_one("p.description")
                descripcion = ""
                if descripcion_el:
                    ver_mas = descripcion_el.select_one("a.ver-mas-btn")
                    if ver_mas:
                        ver_mas.extract()
                    descripcion = descripcion_el.get_text(strip=True)

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo, "empresa": empresa, "remota": remota,
                        "descripcion": descripcion, "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"ChileTrabajos, keyword '{keyword}': {e}")
        yield

    aceptadas = {link: data for link, data in candidatas.items() if not require_remote or data["remota"]}
    if descripcion_completa:
        # Pisa el snippet truncado con la descripción completa de la página.
        yield from _completar_descripciones(aceptadas, on_progreso, debe_detener)

    filas = []
    for link, data in aceptadas.items():
        fila = {
            "sitio": "ChileTrabajos",
            "titulo": data["titulo"],
            "empresa": data["empresa"],
            "keywords_match": ", ".join(sorted(data["keywords"])),
            "remota": "SI" if data["remota"] else "NO",
            "activa": "SI",
            "motivo": "en resultados de búsqueda (activa)",
            "link": link,
            "fecha_creacion": hoy(),
            "descripcion": data["descripcion"],
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# LinkedIn (endpoint público "jobs-guest", sin login — no oficial)
# ---------------------------------------------------------------------------

LINKEDIN_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def escanear_linkedin(keywords: list[str], require_remote: bool, location: str = "Chile", on_progreso=None, debe_detener=None, on_error=None, on_oferta=None, descripcion_completa: bool = False) -> list[dict]:
    candidatas = {}

    for i, keyword in enumerate(keywords, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"'{keyword}' — {len(candidatas)} ofertas encontradas", i, len(keywords))
        try:
            print(f"  → LinkedIn / keyword: {keyword} (location={location})")
            params = {"keywords": keyword, "location": location, "start": "0"}
            if require_remote:
                params["f_WT"] = "2"
            resp = requests.get(
                LINKEDIN_SEARCH_URL,
                headers=HEADERS,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select("div.base-search-card"):
                title_el = card.select_one("h3.base-search-card__title")
                link_el = card.select_one("a.base-card__full-link")
                if not title_el or not link_el:
                    continue
                titulo = title_el.get_text(strip=True)
                link = link_el.get("href", "").split("?")[0]
                empresa_el = card.select_one("h4.base-search-card__subtitle")
                empresa = empresa_el.get_text(strip=True) if empresa_el else ""
                fecha_el = card.select_one("time.job-search-card__listdate")
                fecha = fecha_el.get("datetime") if fecha_el else None

                if link not in candidatas:
                    candidatas[link] = {"titulo": titulo, "empresa": empresa, "fecha": fecha, "keywords": {keyword}}
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"LinkedIn, keyword '{keyword}': {e}")
        yield

    if descripcion_completa:
        yield from _completar_descripciones(candidatas, on_progreso, debe_detener)

    filas = []
    for link, data in candidatas.items():
        # Con require_remote=True, f_WT=2 ya filtró por remoto en la
        # búsqueda; sin ese filtro no sabemos la modalidad por tarjeta,
        # así que queda sin dato ("?") en vez de asumir remoto.
        fila = {
            "sitio": "LinkedIn",
            "titulo": data["titulo"],
            "empresa": data["empresa"],
            "keywords_match": ", ".join(sorted(data["keywords"])),
            "remota": "SI" if require_remote else "?",
            "activa": "SI",
            "motivo": "en resultados de búsqueda (activa)",
            "link": link,
            "fecha_creacion": data["fecha"] or hoy(),
            "descripcion": data.get("descripcion", ""),
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# ElEmpleo (Colombia)
# ---------------------------------------------------------------------------

ELEMPLEO_BASE_URL = "https://www.elempleo.com"


def _elempleo_slug(keyword: str) -> str:
    # A diferencia de los demás sitios, ElEmpleo preserva tildes en el slug
    # (las codifica como %XX en vez de sacarlas), así que no usa
    # slugify_ascii acá — solo reemplaza espacios y deja quote() codificar
    # los caracteres no-ASCII.
    return quote(keyword.strip().lower().replace(" ", "-"))


def escanear_elempleo(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
    candidatas = {}

    for i, keyword in enumerate(keywords, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"'{keyword}' — {len(candidatas)} ofertas encontradas", i, len(keywords))
        slug = _elempleo_slug(keyword)
        # Combinar /trabajo-<keyword> y /modalidad-remoto en la misma URL
        # filtra por ambos a la vez — no hace falta revisar cada tarjeta.
        url = f"{ELEMPLEO_BASE_URL}/co/ofertas-empleo/trabajo-{slug}"
        if require_remote:
            url += "/modalidad-remoto"
        try:
            print(f"  → ElEmpleo / keyword: {keyword}")
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select("div.result-item"):
                title_a = card.select_one("h2.item-title a.js-offer-title")
                if not title_a:
                    continue
                titulo = title_a.get_text(strip=True)
                href = title_a.get("href", "")
                link = urljoin(ELEMPLEO_BASE_URL, href)

                empresa_el = card.select_one("span.js-offer-company")
                empresa = empresa_el.get_text(strip=True) if empresa_el else ""

                # El botón "Compartir" trae la descripción completa en un
                # atributo data-*, sin necesidad de visitar la oferta.
                desc_el = card.select_one("a[data-offer-description]")
                descripcion = desc_el.get("data-offer-description", "").strip() if desc_el else ""

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo, "empresa": empresa, "descripcion": descripcion, "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"ElEmpleo, keyword '{keyword}': {e}")
        yield

    filas = []
    for link, data in candidatas.items():
        # El filtro remoto ya se aplicó en la URL (si require_remote); no
        # hay señal de modalidad confiable por tarjeta para el caso sin filtro.
        fila = {
            "sitio": "ElEmpleo",
            "titulo": data["titulo"],
            "empresa": data["empresa"],
            "keywords_match": ", ".join(sorted(data["keywords"])),
            "remota": "SI" if require_remote else "?",
            "activa": "SI",
            "motivo": "en resultados de búsqueda (activa)",
            "link": link,
            "fecha_creacion": hoy(),
            "descripcion": data["descripcion"],
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# Magneto (Colombia)
# ---------------------------------------------------------------------------

MAGNETO_BASE_URL = "https://www.magneto365.com"


def escanear_magneto(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None, descripcion_completa: bool = False) -> list[dict]:
    candidatas = {}

    for i, keyword in enumerate(keywords, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"'{keyword}' — {len(candidatas)} ofertas encontradas", i, len(keywords))
        slug = slugify_ascii(keyword)
        # /buscar/remoto/<keyword> filtra por ambos (remoto + texto) en una
        # sola request, igual que hicimos con ElEmpleo.
        segmento_modalidad = "remoto/" if require_remote else ""
        url = f"{MAGNETO_BASE_URL}/co/trabajos/buscar/{segmento_modalidad}{slug}"
        try:
            print(f"  → Magneto / keyword: {keyword}")
            resp = requests.get(url, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select('article[class*="magneto-ui-card-jobs"]'):
                title_a = card.select_one("h2 a")
                if not title_a:
                    continue
                titulo = title_a.get_text(strip=True)
                link = urljoin(MAGNETO_BASE_URL, title_a.get("href", ""))

                empresa_el = card.select_one("h3")
                empresa = empresa_el.get_text(strip=True).split("|")[0].strip() if empresa_el else ""

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo, "empresa": empresa, "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"Magneto, keyword '{keyword}': {e}")
        yield

    if descripcion_completa:
        yield from _completar_descripciones(candidatas, on_progreso, debe_detener)

    filas = []
    for link, data in candidatas.items():
        # El filtro remoto ya se aplicó en la URL (si require_remote); sin
        # ese filtro no hay señal de modalidad confiable por tarjeta.
        fila = {
            "sitio": "Magneto",
            "titulo": data["titulo"],
            "empresa": data["empresa"],
            "keywords_match": ", ".join(sorted(data["keywords"])),
            "remota": "SI" if require_remote else "?",
            "activa": "SI",
            "motivo": "en resultados de búsqueda (activa)",
            "link": link,
            "fecha_creacion": hoy(),
            "descripcion": data.get("descripcion", ""),
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# SENA / Agencia Pública de Empleo (Colombia, gobierno)
# ---------------------------------------------------------------------------
# Sitio público y lento — el round robin del scheduler ya ayuda a no
# mandarle una ráfaga seguida de requests (queda intercalado con los demás
# sitios). El link de la oferta funciona sin el jsessionid de sesión que
# trae el botón "Postularme" en la página real.

SENA_BASE_URL = "https://agenciapublicadeempleo.sena.edu.co"


def escanear_sena(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
    candidatas = {}

    for i, keyword in enumerate(keywords, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"'{keyword}' — {len(candidatas)} ofertas encontradas", i, len(keywords))
        try:
            print(f"  → SENA / keyword: {keyword}")
            resp = requests.get(
                f"{SENA_BASE_URL}/spe-web/spe/public/buscadorVacante",
                headers=HEADERS,
                params={"solicitudId": keyword},
                timeout=30,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select("div.tdbuscador"):
                id_el = card.select_one("h4.titulo-color")
                title_el = card.select_one("h5.titulo-color")
                if not id_el or not title_el:
                    continue
                oferta_id = id_el.get_text(strip=True)
                titulo = title_el.get_text(strip=True)
                link = f"{SENA_BASE_URL}/spe-web/spe/demanda/solicitud-sintesis/{oferta_id}"

                texto_card_original = card.get_text(" ", strip=True)
                # "No teletrabajo" contiene "teletrabajo" como substring, por
                # eso se descarta explícitamente ese caso.
                texto_card = texto_card_original.lower()
                remota = "teletrabajo" in texto_card and "no teletrabajo" not in texto_card

                fecha_match = re.search(r"Publicado\s*(\d{2}/\d{2}/\d{4})", texto_card_original)
                fecha = _parsear_fecha(fecha_match.group(1), "%d/%m/%Y") if fecha_match else None

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo, "empresa": "", "remota": remota, "fecha": fecha, "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"SENA, keyword '{keyword}': {e}")
        yield

    filas = []
    for link, data in candidatas.items():
        if require_remote and not data["remota"]:
            continue
        fila = {
            "sitio": "SENA (Colombia)",
            "titulo": data["titulo"],
            "empresa": data["empresa"],
            "keywords_match": ", ".join(sorted(data["keywords"])),
            "remota": "SI" if data["remota"] else "NO",
            "activa": "SI",
            "motivo": "en resultados de búsqueda (activa)",
            "link": link,
            "fecha_creacion": data["fecha"] or hoy(),
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# BNE — Bolsa Nacional de Empleo (Chile, gobierno)
# ---------------------------------------------------------------------------
# API JSON pública, sin login: GET /data/ofertas/buscarListas?textoLibre=<kw>.
# No tiene un filtro de modalidad separado (confirmado: no hay campo de
# "remoto" en la respuesta ni en los facets de "clasificacionOfertas") —
# se detecta buscando "remoto"/"teletrabajo" en título+descripción, texto
# que ya viene completo en la respuesta (no hace falta visitar cada oferta).

BNE_BASE_URL = "https://www.bne.cl"


def _bne_parsear_fecha(fecha_str: str) -> str | None:
    # "fecha" viene como "15/07/26" (DD/MM/AA)
    try:
        dia, mes, anio = fecha_str.split("/")
        return f"20{anio}-{mes}-{dia}"
    except (ValueError, AttributeError):
        return None


def escanear_bne(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
    candidatas = {}

    for i, keyword in enumerate(keywords, 1):
        if debe_detener and debe_detener():
            break
        if on_progreso:
            on_progreso(f"'{keyword}' — {len(candidatas)} ofertas encontradas", i, len(keywords))
        try:
            print(f"  → BNE / keyword: {keyword}")
            resp = requests.get(
                f"{BNE_BASE_URL}/data/ofertas/buscarListas",
                headers={
                    **HEADERS,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                },
                params={"mostrar": "empleo", "textoLibre": keyword, "numResultadosPorPagina": 10, "clasificarYPaginar": "true"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("paginaOfertas", {}).get("resultados", []):
                titulo = html.unescape(item.get("titulo", ""))
                descripcion = html.unescape(item.get("descripcion", "") or "")
                empresa = html.unescape(item.get("empresa", "") or "")
                codigo = item.get("codigo", "")
                if not codigo:
                    continue
                link = f"{BNE_BASE_URL}/oferta/{codigo}"

                texto = f"{titulo} {descripcion}".lower()
                remota = "remoto" in texto or "teletrabajo" in texto
                fecha = _bne_parsear_fecha(item.get("fecha", ""))

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo, "empresa": empresa, "remota": remota,
                        "fecha": fecha, "descripcion": descripcion, "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except (requests.RequestException, ValueError) as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"BNE, keyword '{keyword}': {e}")
        yield

    filas = []
    for link, data in candidatas.items():
        if require_remote and not data["remota"]:
            continue
        fila = {
            "sitio": "BNE (Chile)",
            "titulo": data["titulo"],
            "empresa": data["empresa"],
            "keywords_match": ", ".join(sorted(data["keywords"])),
            "remota": "SI" if data["remota"] else "NO",
            "activa": "SI",
            "motivo": "en resultados de búsqueda (activa)",
            "link": link,
            "fecha_creacion": data["fecha"] or hoy(),
            "descripcion": data["descripcion"],
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# Registro de sitios por país
# ---------------------------------------------------------------------------
# Para agregar un país nuevo: escribir sus funciones escanear_<sitio>()
# (mismo contrato: (keywords, require_remote[, location]) -> list[dict]
# con las claves de CSV_FIELDS salvo "sitio", que se completa acá), sumarlas
# a SITIOS, y agregar la entrada correspondiente en PAISES.

# "soporta_descripcion_completa": True marca los sitios cuyo buscador NO
# trae ya la descripción completa (o solo trae un snippet corto) — para
# esos, --descripcion-completa visita cada oferta aceptada y saca la
# descripción real con los extractores de extraer_oferta.py. Laborum, BNE,
# ElEmpleo y GetOnBrd no lo necesitan (ya la traen gratis); SENA queda
# afuera por ahora (no tiene extractor de detalle todavía).
SITIOS = [
    {"id": "getonbrd", "nombre": "GetOnBrd", "fn": escanear_getonbrd, "usa_location": True},
    {"id": "computrabajo_cl", "nombre": "Computrabajo", "fn": escanear_computrabajo, "soporta_descripcion_completa": True,
     "kwargs_extra": {"base_url": "https://cl.computrabajo.com"}},
    {"id": "laborum_cl", "nombre": "Laborum", "fn": escanear_laborum},
    {"id": "trabajando_cl", "nombre": "Trabajando.cl", "fn": escanear_trabajando, "soporta_descripcion_completa": True},
    {"id": "chiletrabajos_cl", "nombre": "ChileTrabajos", "fn": escanear_chiletrabajos, "soporta_descripcion_completa": True},
    {"id": "linkedin", "nombre": "LinkedIn", "fn": escanear_linkedin, "usa_location": True, "soporta_descripcion_completa": True},
    {"id": "computrabajo_co", "nombre": "Computrabajo Colombia", "fn": escanear_computrabajo, "soporta_descripcion_completa": True,
     "kwargs_extra": {"base_url": "https://co.computrabajo.com", "nombre_sitio": "Computrabajo Colombia"}},
    {"id": "elempleo_co", "nombre": "ElEmpleo", "fn": escanear_elempleo},
    {"id": "getonbrd_co", "nombre": "GetOnBrd Colombia", "fn": escanear_getonbrd, "usa_location": True,
     "kwargs_extra": {"nombre_sitio": "GetOnBrd Colombia"}},
    {"id": "magneto_co", "nombre": "Magneto", "fn": escanear_magneto, "soporta_descripcion_completa": True},
    {"id": "sena_co", "nombre": "SENA (Colombia)", "fn": escanear_sena},
    {"id": "bne_cl", "nombre": "BNE (Chile)", "fn": escanear_bne},
]
SITIOS_POR_ID = {s["id"]: s for s in SITIOS}

PAISES = {
    "Chile": ["getonbrd", "computrabajo_cl", "laborum_cl", "trabajando_cl", "chiletrabajos_cl", "bne_cl", "linkedin"],
    "Colombia": ["computrabajo_co", "elempleo_co", "getonbrd_co", "magneto_co", "sena_co", "linkedin"],
    # Bumeran Colombia y Konzerta quedan pendientes: son SPAs, falta encontrar
    # su API real (mismo proceso que se usó para Laborum/Trabajando.cl).
}


def ejecutar_sitios(ids_sitios: list[str], keywords: list[str], require_remote: bool, pais: str,
                     on_sitio=None, on_paso=None, on_error=None, on_oferta=None, debe_detener=None,
                     descripcion_completa: bool = False) -> tuple[list[dict], set]:
    """Corre los adaptadores pedidos en **round robin**: en vez de terminar
    un sitio entero antes de pasar al siguiente, se le pide un request a
    cada sitio por turno (cada escanear_<sitio>() es un generador que hace
    yield después de cada request individual). Esto reparte la carga entre
    sitios en vez de mandarle a uno solo una ráfaga larga de requests
    seguidos — mismo delay total, pero cada sitio individual respira más
    entre sus propios requests.

    - on_sitio(nombre_sitio): se llama al arrancar el generador de cada sitio.
    - on_paso(nombre_sitio, etiqueta, i, total): progreso fino (por keyword o
      categoría) dentro de un sitio.
    - on_error(nombre_sitio, mensaje): se llama en cada request que falla,
      sin cortar el scan (la GUI lo usa para mostrar un log de errores).
    - on_oferta(fila): se llama por cada oferta que pasa los filtros (ya
      trae "sitio" adentro). En GetOnBrd es oferta por oferta; en el resto
      de los sitios llega en tanda al terminar ese sitio (agrupan
      keywords_match por link antes de poder filtrar por remoto).
    - debe_detener(): si existe y devuelve True, corta el scan lo antes
      posible (entre un request y el siguiente, de cualquier sitio).
    - descripcion_completa: si es True, los sitios marcados con
      "soporta_descripcion_completa" visitan cada oferta aceptada para
      sacar su descripción real (más lento). El resto de los sitios ni
      siquiera recibe este kwarg — sus funciones no lo aceptan."""
    sitios_habilitados = set()
    generadores = []

    for sitio_id in ids_sitios:
        sitio = SITIOS_POR_ID[sitio_id]
        nombre_sitio = sitio["nombre"]
        sitios_habilitados.add(nombre_sitio)
        if on_sitio:
            on_sitio(nombre_sitio)

        _on_paso = (lambda etiqueta, i, total, _n=nombre_sitio: on_paso(_n, etiqueta, i, total)) if on_paso else None
        _on_error = (lambda mensaje, _n=nombre_sitio: on_error(_n, mensaje)) if on_error else None
        kwargs_extra = dict(sitio.get("kwargs_extra", {}))
        if descripcion_completa and sitio.get("soporta_descripcion_completa"):
            kwargs_extra["descripcion_completa"] = True

        if sitio.get("usa_location"):
            gen = sitio["fn"](keywords, require_remote, location=pais, on_progreso=_on_paso, debe_detener=debe_detener, on_error=_on_error, on_oferta=on_oferta, **kwargs_extra)
        else:
            gen = sitio["fn"](keywords, require_remote, on_progreso=_on_paso, debe_detener=debe_detener, on_error=_on_error, on_oferta=on_oferta, **kwargs_extra)
        generadores.append({"filas": [], "gen": gen})

    # Al detener NO se abandonan los generadores: cada uno revisa
    # debe_detener() al retomar, sale de su loop sin hacer más requests y
    # devuelve (vía StopIteration) las ofertas que ya había encontrado.
    # Cortar el while de golpe, como se hacía antes, tiraba esas ofertas.
    detenido = False
    activos = list(generadores)
    while activos:
        siguientes = []
        for entry in activos:
            if not detenido and debe_detener and debe_detener():
                detenido = True
            try:
                next(entry["gen"])
                siguientes.append(entry)
            except StopIteration as fin:
                entry["filas"] = fin.value or []
            if not detenido:
                time.sleep(REQUEST_DELAY_SECONDS)
        activos = siguientes

    filas = []
    for entry in generadores:
        filas += entry["filas"]
    return filas, sitios_habilitados


def inicio_valido(inicio_ts: float | None, ahora: float | None = None, tolerancia: float = 3600) -> float:
    """Hora de inicio que pasa la GUI con --inicio-ts (cuando lanzó el job,
    antes de que el contenedor arrancara). Si falta o es absurda (en el
    futuro, o de hace más de `tolerancia` segundos), se usa la hora actual."""
    ahora = time.time() if ahora is None else ahora
    if inicio_ts is None or inicio_ts > ahora or ahora - inicio_ts > tolerancia:
        return ahora
    return inicio_ts


def estado_inicial(pais: str, sitios: list[str], ahora: float | None = None, pid: int | None = None) -> dict:
    """Estado de un scan recién lanzado. Lo usa EscritorEstado al arrancar y
    la GUI cuando lanza el scan en la nube: el job tarda en arrancar, y
    escribiendo esto antes la GUI muestra "corriendo" de inmediato en vez de
    seguir mostrando los resultados del scan anterior."""
    ahora = time.time() if ahora is None else ahora
    return {
        "corriendo": True,
        "pid": os.getpid() if pid is None else pid,
        "inicio_ts": ahora,
        "actualizado_ts": ahora,
        "pais": pais,
        "sitios": sitios,
        "progreso": {"sitio": "", "etiqueta": "", "i": 0, "total": 0},
        "pasos_completados": 0,
        "pasos_totales_por_sitio": {},
        "log": [],
        "errores": [],
        "ofertas_encontradas": [],
        "resultado": None,
    }


class EscritorEstado:
    """Escribe el progreso del scan a un archivo JSON en disco (atómico vía
    escribir-y-renombrar), y ofrece un chequeo de "detener" basado en la
    existencia de un archivo flag. Pensado para que el scan pueda correr
    como proceso de sistema operativo aparte (via CLI) mientras una GUI
    en otro proceso (o el mismo, reiniciado) lee ese archivo para mostrar
    el progreso — así el scan sobrevive a un reinicio de la GUI."""

    MAX_LINEAS_LOG = 300  # tope de líneas que se guardan (y por lo tanto se
    # muestran) del log en vivo — sin esto, el status JSON crece sin límite
    # durante todo el scan (un append por cada request, en los ~700+ pasos
    # de GetOnBrd por ejemplo) y cada escritura reescribe el archivo entero.

    def __init__(self, almacen: Almacen, status_file: str, detener_file: str | None, pais: str, sitios: list[str],
                 min_intervalo_guardado: float = 0, intervalo_detener: float = 0, inicio: float | None = None):
        """min_intervalo_guardado: segundos mínimos entre subidas del
        estado (0 = en cada paso, como en disco local). En un bucket cada
        subida es una operación de escritura con cuota gratis limitada, así
        que ahí se espacia (los guardados finales siempre se fuerzan).
        intervalo_detener: cuánto se recuerda el último chequeo del flag de
        detener, por la misma razón (una lectura al bucket por paso)."""
        self.almacen = almacen
        self.status_file = status_file
        self.detener_file = detener_file
        self.min_intervalo_guardado = min_intervalo_guardado
        self.intervalo_detener = intervalo_detener
        self._ultimo_guardado = 0.0
        self._ultimo_chequeo_detener = (0.0, False)
        self.inicio = time.time() if inicio is None else inicio
        self.estado = estado_inicial(pais, sitios, self.inicio)
        self._guardar(forzar=True)

    def _guardar(self, forzar: bool = False):
        ahora = time.time()
        if not forzar and ahora - self._ultimo_guardado < self.min_intervalo_guardado:
            return
        self.estado["actualizado_ts"] = ahora
        try:
            self.almacen.escribir_texto(self.status_file, json.dumps(self.estado, ensure_ascii=False))
            self._ultimo_guardado = ahora
        except Exception as e:
            # Una escritura de progreso que falla (archivo bloqueado por la
            # GUI en Windows, un corte de red hacia el bucket) no debe
            # tumbar un scan de 40 minutos: se reintenta en el próximo paso.
            print(f"    ⚠ no se pudo actualizar {self.almacen.descripcion(self.status_file)} ({type(e).__name__}), se sigue igual", file=sys.stderr)

    def _log(self, linea: str):
        self.estado["log"].append(linea)
        self.estado["log"] = self.estado["log"][-self.MAX_LINEAS_LOG:]

    def on_sitio(self, nombre_sitio):
        self.estado["progreso"] = {"sitio": nombre_sitio, "etiqueta": "", "i": 0, "total": 0}
        self._log(f"--- {nombre_sitio} ---")
        self._guardar()

    def on_paso(self, nombre_sitio, etiqueta, i, total):
        self.estado["progreso"] = {"sitio": nombre_sitio, "etiqueta": etiqueta, "i": i, "total": total}
        # Avance global: un contador que nunca resetea (un paso más, sea
        # de cualquier sitio) y el total conocido por sitio, que se va
        # afinando a medida que cada uno revela el suyo (puede cambiar
        # entre fases de un mismo sitio, ej. GetOnBrd: 6 categorías al
        # principio, después ~350+ ofertas individuales).
        self.estado["pasos_completados"] += 1
        self.estado["pasos_totales_por_sitio"][nombre_sitio] = total
        self._log(f"[{nombre_sitio}] {etiqueta}")
        self._guardar()

    def on_error(self, nombre_sitio, mensaje):
        # Los errores se guardan aparte del log general (que se cappea a
        # MAX_LINEAS_LOG) para que el resumen final de errores no pierda
        # los que quedaron viejos por delante de un scan largo.
        self.estado["errores"].append(f"⚠ [{nombre_sitio}] {mensaje}")
        self._log(f"⚠ [{nombre_sitio}] {mensaje}")
        self._guardar()

    def on_oferta(self, fila):
        self.estado["ofertas_encontradas"].append(fila)
        self._guardar()

    def debe_detener(self) -> bool:
        if not self.detener_file:
            return False
        ahora = time.time()
        ultimo_ts, ultimo_valor = self._ultimo_chequeo_detener
        if ultimo_valor or ahora - ultimo_ts < self.intervalo_detener:
            return ultimo_valor
        try:
            valor = self.almacen.existe(self.detener_file)
        except Exception:
            valor = False  # sin poder leer el flag, no cortar el scan por las dudas
        self._ultimo_chequeo_detener = (ahora, valor)
        return valor

    def finalizar(self, filas: list[dict], filas_finales: list[dict]):
        self.estado["corriendo"] = False
        self.estado["resultado"] = {
            "nuevas": len(filas),
            "total": len(filas_finales),
            "activas": len([f for f in filas_finales if f["activa"] == "SI"]),
            "detenido": self.debe_detener(),
            "duracion_seg": time.time() - self.inicio,
        }
        self._guardar(forzar=True)
        if self.detener_file:
            self.almacen.borrar(self.detener_file)

    def registrar_correo(self, error: str | None):
        self.estado["correo"] = {"enviado": error is None, "error": error}
        self._guardar(forzar=True)


def guardar_resultados(filas: list[dict], sitios_habilitados: set, almacen: Almacen | None = None) -> list[dict]:
    """Escribe filas en OUTPUT_CSV conservando las filas ya guardadas de
    sitios que no están en sitios_habilitados. Devuelve la lista completa
    final (previas + nuevas).

    La fecha_creacion de cada oferta se preserva entre escaneos: si un link
    ya existía (de cualquier escaneo anterior, esté o no su sitio en este
    escaneo), se mantiene su primera fecha vista en vez de pisarla con la
    de hoy — así se puede saber hace cuánto está esa oferta, no solo
    cuándo se vio por última vez."""
    almacen = almacen or AlmacenLocal()
    filas_previas = []
    fecha_por_link = {}
    texto_previo = almacen.leer_texto(OUTPUT_CSV)
    if texto_previo is not None:
        todas_previas = list(csv.DictReader(io.StringIO(texto_previo, newline="")))
        filas_previas = [r for r in todas_previas if r["sitio"] not in sitios_habilitados]
        fecha_por_link = {r["link"]: r["fecha_creacion"] for r in todas_previas if r.get("fecha_creacion")}

    for fila in filas:
        if fila["link"] in fecha_por_link:
            fila["fecha_creacion"] = fecha_por_link[fila["link"]]

    filas_finales = filas_previas + filas

    salida = io.StringIO(newline="")
    writer = csv.DictWriter(salida, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(filas_finales)
    almacen.escribir_texto(OUTPUT_CSV, salida.getvalue())

    return filas_finales


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scanner de ofertas de empleo multi-sitio/país")
    parser.add_argument("--pais", default="Chile", help=f"País a escanear. Disponibles: {', '.join(PAISES)}")
    parser.add_argument("--solo", help="Ids de sitio separados por coma para correr solo esos (ej: laborum_cl)")
    parser.add_argument("--keywords-file", default=KEYWORDS_FILE_DEFAULT, help="Archivo con keywords, una por línea")
    parser.add_argument("--generar-prompt-keywords", action="store_true",
                         help="Imprime un prompt para pegar en Claude y generar keywords.txt; no escanea nada")
    parser.add_argument("--perfil", default=PERFIL_FILE_DEFAULT, help="Archivo de perfil/CV para el prompt de keywords")
    parser.add_argument("--incluir-no-remotas", action="store_true", help="No filtrar por remoto (por defecto solo trae remotas)")
    parser.add_argument("--descripcion-completa", action="store_true",
                         help="Visita cada oferta aceptada en Computrabajo, ChileTrabajos, LinkedIn y Magneto para sacar la descripción real (más lento). GetOnBrd, Laborum, BNE y ElEmpleo ya la traen sin este flag")
    parser.add_argument("--email-a", help=f"Si se pasa, manda un correo a esta dirección al terminar (credenciales SMTP en {EMAIL_CONFIG_FILE})")
    parser.add_argument("--status-file", help="Si se pasa, escribe el progreso en vivo a este JSON (lo usa la GUI para leer el estado desde otro proceso)")
    parser.add_argument("--detener-file", help="Si este archivo existe durante el scan, se corta apenas se detecta (requiere --status-file)")
    parser.add_argument("--inicio-ts", type=float, help="Hora (epoch) en que la GUI lanzó este scan; el contador de tiempo parte de ahí y no del arranque del contenedor")
    parser.add_argument("--storage", help="Dónde viven keywords, estado, flag de detener y CSV de resultados: gs://bucket/prefijo para Cloud Storage. Si no se pasa, archivos en disco como siempre")
    args = parser.parse_args()
    try:
        almacen = crear_almacen(args.storage)
    except ErrorAlmacen as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    en_la_nube = bool(args.storage)

    if args.generar_prompt_keywords:
        generar_prompt_keywords(args.perfil)
        return

    if args.pais not in PAISES:
        print(f"País no soportado: '{args.pais}'. Disponibles: {', '.join(PAISES)}", file=sys.stderr)
        sys.exit(1)

    ids_sitios = PAISES[args.pais]
    if args.solo:
        pedidos = {s.strip() for s in args.solo.split(",")}
        desconocidos = pedidos - set(SITIOS_POR_ID)
        if desconocidos:
            print(f"Ids de sitio desconocidos: {', '.join(desconocidos)}. Disponibles: {', '.join(SITIOS_POR_ID)}", file=sys.stderr)
            sys.exit(1)
        ids_sitios = [i for i in ids_sitios if i in pedidos]

    keywords = cargar_keywords(args.keywords_file, almacen)
    require_remote = not args.incluir_no_remotas

    inicio = inicio_valido(args.inicio_ts)
    print(f"[{datetime.now():%H:%M:%S}] Iniciando escaneo — país: {args.pais}, sitios: {', '.join(ids_sitios)}")
    print(f"Keywords ({len(keywords)}): {', '.join(keywords)}")

    nombres_sitios = [SITIOS_POR_ID[i]["nombre"] for i in ids_sitios]
    escritor = None
    if args.status_file:
        escritor = EscritorEstado(
            almacen, args.status_file, args.detener_file, args.pais, nombres_sitios,
            min_intervalo_guardado=INTERVALO_GUARDADO_NUBE if en_la_nube else 0,
            intervalo_detener=INTERVALO_DETENER_NUBE if en_la_nube else 0,
            inicio=inicio,
        )

    def _log_progreso(nombre_sitio):
        print(f"[{datetime.now():%H:%M:%S}] {nombre_sitio}...")
        if escritor:
            escritor.on_sitio(nombre_sitio)

    filas, sitios_habilitados = ejecutar_sitios(
        ids_sitios, keywords, require_remote, args.pais,
        on_sitio=_log_progreso,
        on_paso=escritor.on_paso if escritor else None,
        on_error=escritor.on_error if escritor else None,
        on_oferta=escritor.on_oferta if escritor else None,
        debe_detener=escritor.debe_detener if escritor else None,
        descripcion_completa=args.descripcion_completa,
    )

    for i, f in enumerate(filas, 1):
        print(f"  [{i}/{len(filas)}] [{f['sitio']}] {f['titulo'][:60]} — {f['motivo']}")

    filas_finales = guardar_resultados(filas, sitios_habilitados, almacen)

    if escritor:
        escritor.finalizar(filas, filas_finales)

    duracion = time.time() - inicio
    activas = [f for f in filas_finales if f["activa"] == "SI"]
    print(f"\n[{datetime.now():%H:%M:%S}] Listo en {formatear_duracion(duracion)}. {len(activas)}/{len(filas_finales)} ofertas activas y relevantes en total ({len(filas)} nuevas de este escaneo).")
    print(f"Resultado guardado en: {almacen.descripcion(OUTPUT_CSV)}")

    if args.email_a:
        detenido = escritor.estado["resultado"]["detenido"] if escritor else False
        errores = escritor.estado["errores"] if escritor else []
        asunto, cuerpo = armar_correo_fin(args.pais, filas, filas_finales, duracion, detenido, errores)
        csv_final = almacen.leer(OUTPUT_CSV)
        error_correo = enviar_correo_fin(args.email_a, asunto, cuerpo, adjuntos=[(OUTPUT_CSV, csv_final)] if csv_final else None)
        if error_correo:
            print(f"⚠ No se pudo enviar el correo a {args.email_a}: {error_correo}", file=sys.stderr)
        else:
            print(f"Correo enviado a {args.email_a}")
        if escritor:
            escritor.registrar_correo(error_correo)


if __name__ == "__main__":
    main()
