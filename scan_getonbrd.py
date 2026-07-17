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
import unicodedata
from datetime import datetime
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

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
CSV_FIELDS = ["sitio", "titulo", "empresa", "keywords_match", "remota", "activa", "motivo", "link", "fecha_creacion"]


def hoy() -> str:
    return datetime.now().strftime("%Y-%m-%d")


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


def cargar_keywords(path: str = KEYWORDS_FILE_DEFAULT) -> list[str]:
    """Lee keywords desde un archivo (una por línea, '#' comenta la línea).
    Si no existe o queda vacío, usa DEFAULT_KEYWORDS."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            kws = [linea.strip() for linea in f if linea.strip() and not linea.strip().startswith("#")]
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
        titulo = card.get_text(strip=True)
        if not titulo:
            continue
        link = urljoin(GETONBRD_BASE_URL, href)
        ofertas.append({"titulo": titulo, "link": link})

    return ofertas


def _getonbrd_inspeccionar_oferta(link: str) -> dict:
    try:
        resp = requests.get(link, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"activa": False, "motivo": f"error de red: {e}", "remota": False, "texto": ""}

    texto = resp.text.lower()

    for señal in GETONBRD_SEÑALES_CERRADA:
        if señal in texto:
            return {"activa": False, "motivo": f"cerrada (detectado: '{señal}')", "remota": False, "texto": texto}

    remota = any(señal in texto for señal in GETONBRD_SEÑALES_REMOTO)
    return {"activa": True, "motivo": "activa", "remota": remota, "texto": texto}


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
            "empresa": "",
            "keywords_match": ", ".join(matches),
            "remota": "SI" if info["remota"] else "NO",
            "activa": "SI" if info["activa"] else "NO",
            "motivo": info["motivo"],
            "link": link,
            "fecha_creacion": hoy(),
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)

    return filas


# ---------------------------------------------------------------------------
# Computrabajo (misma plataforma en varios países — cambia el subdominio)
# ---------------------------------------------------------------------------

COMPUTRABAJO_BASE_URL = "https://cl.computrabajo.com"  # default, se puede pasar otro por base_url


def escanear_computrabajo(keywords: list[str], require_remote: bool, base_url: str = COMPUTRABAJO_BASE_URL, nombre_sitio: str = "Computrabajo", on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
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

    filas = []
    for link, data in candidatas.items():
        if require_remote and not data["remota"]:
            continue
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

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo,
                        "empresa": empresa,
                        "remota": remota,
                        "fecha": fecha,
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
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# Trabajando.cl
# ---------------------------------------------------------------------------

TRABAJANDO_SEARCH_URL = "https://www.trabajando.cl/api/searchjob"


def escanear_trabajando(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
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

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo,
                        "empresa": item.get("nombreEmpresa", "") or "",
                        "remota": remota,
                        "fecha": fecha,
                        "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"Trabajando.cl, keyword '{keyword}': {e}")
        yield

    filas = []
    for link, data in candidatas.items():
        if require_remote and not data["remota"]:
            continue
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


def escanear_chiletrabajos(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
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

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo, "empresa": empresa, "remota": remota, "keywords": {keyword},
                    }
                else:
                    candidatas[link]["keywords"].add(keyword)
        except requests.RequestException as e:
            print(f"    ⚠ error buscando '{keyword}': {e}", file=sys.stderr)
            if on_error:
                on_error(f"ChileTrabajos, keyword '{keyword}': {e}")
        yield

    filas = []
    for link, data in candidatas.items():
        if require_remote and not data["remota"]:
            continue
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
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# LinkedIn (endpoint público "jobs-guest", sin login — no oficial)
# ---------------------------------------------------------------------------

LINKEDIN_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def escanear_linkedin(keywords: list[str], require_remote: bool, location: str = "Chile", on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
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

                if link not in candidatas:
                    candidatas[link] = {
                        "titulo": titulo, "empresa": empresa, "keywords": {keyword},
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
        }
        filas.append(fila)
        if on_oferta:
            on_oferta(fila)
    return filas


# ---------------------------------------------------------------------------
# Magneto (Colombia)
# ---------------------------------------------------------------------------

MAGNETO_BASE_URL = "https://www.magneto365.com"


def escanear_magneto(keywords: list[str], require_remote: bool, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None) -> list[dict]:
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
                        "fecha": fecha, "keywords": {keyword},
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

SITIOS = [
    {"id": "getonbrd", "nombre": "GetOnBrd", "fn": escanear_getonbrd, "usa_location": True},
    {"id": "computrabajo_cl", "nombre": "Computrabajo", "fn": escanear_computrabajo,
     "kwargs_extra": {"base_url": "https://cl.computrabajo.com"}},
    {"id": "laborum_cl", "nombre": "Laborum", "fn": escanear_laborum},
    {"id": "trabajando_cl", "nombre": "Trabajando.cl", "fn": escanear_trabajando},
    {"id": "chiletrabajos_cl", "nombre": "ChileTrabajos", "fn": escanear_chiletrabajos},
    {"id": "linkedin", "nombre": "LinkedIn", "fn": escanear_linkedin, "usa_location": True},
    {"id": "computrabajo_co", "nombre": "Computrabajo Colombia", "fn": escanear_computrabajo,
     "kwargs_extra": {"base_url": "https://co.computrabajo.com", "nombre_sitio": "Computrabajo Colombia"}},
    {"id": "elempleo_co", "nombre": "ElEmpleo", "fn": escanear_elempleo},
    {"id": "getonbrd_co", "nombre": "GetOnBrd Colombia", "fn": escanear_getonbrd, "usa_location": True,
     "kwargs_extra": {"nombre_sitio": "GetOnBrd Colombia"}},
    {"id": "magneto_co", "nombre": "Magneto", "fn": escanear_magneto},
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
                     on_sitio=None, on_paso=None, on_error=None, on_oferta=None, debe_detener=None) -> tuple[list[dict], set]:
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
      posible (entre un request y el siguiente, de cualquier sitio)."""
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
        kwargs_extra = sitio.get("kwargs_extra", {})

        if sitio.get("usa_location"):
            gen = sitio["fn"](keywords, require_remote, location=pais, on_progreso=_on_paso, debe_detener=debe_detener, on_error=_on_error, on_oferta=on_oferta, **kwargs_extra)
        else:
            gen = sitio["fn"](keywords, require_remote, on_progreso=_on_paso, debe_detener=debe_detener, on_error=_on_error, on_oferta=on_oferta, **kwargs_extra)
        generadores.append({"filas": [], "gen": gen})

    activos = list(generadores)
    while activos:
        if debe_detener and debe_detener():
            break
        siguientes = []
        for entry in activos:
            try:
                next(entry["gen"])
                siguientes.append(entry)
            except StopIteration as fin:
                entry["filas"] = fin.value or []
            time.sleep(REQUEST_DELAY_SECONDS)
            if debe_detener and debe_detener():
                break
        activos = siguientes

    filas = []
    for entry in generadores:
        filas += entry["filas"]
    return filas, sitios_habilitados


class EscritorEstado:
    """Escribe el progreso del scan a un archivo JSON en disco (atómico vía
    escribir-y-renombrar), y ofrece un chequeo de "detener" basado en la
    existencia de un archivo flag. Pensado para que el scan pueda correr
    como proceso de sistema operativo aparte (via CLI) mientras una GUI
    en otro proceso (o el mismo, reiniciado) lee ese archivo para mostrar
    el progreso — así el scan sobrevive a un reinicio de la GUI."""

    def __init__(self, status_file: str, detener_file: str | None, pais: str, sitios: list[str]):
        self.status_file = status_file
        self.detener_file = detener_file
        self.estado = {
            "corriendo": True,
            "pid": os.getpid(),
            "pais": pais,
            "sitios": sitios,
            "progreso": {"sitio": "", "etiqueta": "", "i": 0, "total": 0},
            "log": [],
            "ofertas_encontradas": [],
            "resultado": None,
        }
        self._guardar()

    def _guardar(self):
        tmp = f"{self.status_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.estado, f, ensure_ascii=False)
        os.replace(tmp, self.status_file)

    def on_sitio(self, nombre_sitio):
        self.estado["progreso"] = {"sitio": nombre_sitio, "etiqueta": "", "i": 0, "total": 0}
        self.estado["log"].append(f"--- {nombre_sitio} ---")
        self._guardar()

    def on_paso(self, nombre_sitio, etiqueta, i, total):
        self.estado["progreso"] = {"sitio": nombre_sitio, "etiqueta": etiqueta, "i": i, "total": total}
        self.estado["log"].append(f"[{nombre_sitio}] {etiqueta}")
        self._guardar()

    def on_error(self, nombre_sitio, mensaje):
        self.estado["log"].append(f"⚠ [{nombre_sitio}] {mensaje}")
        self._guardar()

    def on_oferta(self, fila):
        self.estado["ofertas_encontradas"].append(fila)
        self._guardar()

    def debe_detener(self) -> bool:
        return bool(self.detener_file and os.path.exists(self.detener_file))

    def finalizar(self, filas: list[dict], filas_finales: list[dict]):
        self.estado["corriendo"] = False
        self.estado["resultado"] = {
            "nuevas": len(filas),
            "total": len(filas_finales),
            "activas": len([f for f in filas_finales if f["activa"] == "SI"]),
            "detenido": self.debe_detener(),
        }
        self._guardar()
        if self.detener_file and os.path.exists(self.detener_file):
            os.remove(self.detener_file)


def guardar_resultados(filas: list[dict], sitios_habilitados: set) -> list[dict]:
    """Escribe filas en OUTPUT_CSV conservando las filas ya guardadas de
    sitios que no están en sitios_habilitados. Devuelve la lista completa
    final (previas + nuevas).

    La fecha_creacion de cada oferta se preserva entre corridas: si un link
    ya existía (de cualquier corrida anterior, esté o no su sitio en esta
    corrida), se mantiene su primera fecha vista en vez de pisarla con la
    de hoy — así se puede saber hace cuánto está esa oferta, no solo
    cuándo se vio por última vez."""
    filas_previas = []
    fecha_por_link = {}
    if os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
            todas_previas = list(csv.DictReader(f))
        filas_previas = [r for r in todas_previas if r["sitio"] not in sitios_habilitados]
        fecha_por_link = {r["link"]: r["fecha_creacion"] for r in todas_previas if r.get("fecha_creacion")}

    for fila in filas:
        if fila["link"] in fecha_por_link:
            fila["fecha_creacion"] = fecha_por_link[fila["link"]]

    filas_finales = filas_previas + filas

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(filas_finales)

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
    parser.add_argument("--status-file", help="Si se pasa, escribe el progreso en vivo a este JSON (lo usa la GUI para leer el estado desde otro proceso)")
    parser.add_argument("--detener-file", help="Si este archivo existe durante el scan, se corta apenas se detecta (requiere --status-file)")
    args = parser.parse_args()

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

    keywords = cargar_keywords(args.keywords_file)
    require_remote = not args.incluir_no_remotas

    print(f"[{datetime.now():%H:%M:%S}] Iniciando escaneo — país: {args.pais}, sitios: {', '.join(ids_sitios)}")
    print(f"Keywords ({len(keywords)}): {', '.join(keywords)}")

    nombres_sitios = [SITIOS_POR_ID[i]["nombre"] for i in ids_sitios]
    escritor = EscritorEstado(args.status_file, args.detener_file, args.pais, nombres_sitios) if args.status_file else None

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
    )

    for i, f in enumerate(filas, 1):
        print(f"  [{i}/{len(filas)}] [{f['sitio']}] {f['titulo'][:60]} — {f['motivo']}")

    filas_finales = guardar_resultados(filas, sitios_habilitados)

    if escritor:
        escritor.finalizar(filas, filas_finales)

    activas = [f for f in filas_finales if f["activa"] == "SI"]
    print(f"\n[{datetime.now():%H:%M:%S}] Listo. {len(activas)}/{len(filas_finales)} ofertas activas y relevantes en total ({len(filas)} nuevas de esta corrida).")
    print(f"Resultado guardado en: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
