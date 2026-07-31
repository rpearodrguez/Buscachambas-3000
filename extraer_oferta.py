"""
Extractor de descripción completa de una oferta
------------------------------------------------
Dado el link de una oferta (de las que salen en ofertas_empleos.csv),
descarga la página y saca el texto completo y limpio (título, empresa,
descripción) listo para pegar en el Project de Claude que arma
currículums.

Cubre GetOnBrd, Computrabajo (Chile y Colombia), ChileTrabajos, ElEmpleo,
Magneto, BNE, LinkedIn y Trabajando.cl. Laborum no lo necesita: su propia
búsqueda ya trae la descripción completa (ver escanear_laborum en
scan_getonbrd.py), así que nunca hace falta visitar la oferta aparte.

USO:
    python extraer_oferta.py "<link-de-la-oferta>"
"""

import json
import re
import sys
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _extraer_getonbrd(soup: BeautifulSoup) -> str:
    body = soup.select_one("div#job-body")
    return body.get_text("\n", strip=True) if body else ""


def _extraer_computrabajo(soup: BeautifulSoup) -> str:
    parrafo = soup.select_one('div[div-link="oferta"] p.mbB')
    return parrafo.get_text("\n", strip=True) if parrafo else ""


def _extraer_linkedin(soup: BeautifulSoup) -> str:
    desc = soup.select_one("div.show-more-less-html__markup")
    return desc.get_text("\n", strip=True) if desc else ""


def _extraer_chiletrabajos(soup: BeautifulSoup) -> str:
    parrafo = soup.select_one("div.p-x-3.overflow-hidden p.mb-0")
    return parrafo.get_text("\n", strip=True) if parrafo else ""


def _extraer_elempleo(soup: BeautifulSoup) -> str:
    parrafo = soup.select_one("div.description-block p.mb-0")
    return parrafo.get_text("\n", strip=True) if parrafo else ""


def _extraer_magneto(soup: BeautifulSoup) -> str:
    contenedor = soup.select_one('div[class*="JobOfferDetailContent_content"]')
    return contenedor.get_text("\n", strip=True) if contenedor else ""


def _titulo_magneto(soup: BeautifulSoup) -> str:
    # Magneto no usa <h1> en la página de detalle — el título real es el
    # primer <h6> dentro del contenedor de la descripción.
    h6 = soup.select_one('div[class*="JobOfferDetailContent_content"] h6')
    return h6.get_text(strip=True) if h6 else ""


def _bne_descripcion_soup(soup: BeautifulSoup) -> BeautifulSoup | None:
    # BNE embebe un bloque schema.org JobPosting con la descripción
    # completa como HTML dentro del JSON (no en el HTML de la página en
    # sí) — el <h1> de la página mezcla título + categoría + ocupación
    # todo junto, así que el título real también sale de acá.
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            # BNE mete \r\n literales (sin escapar) dentro del string JSON,
            # que es inválido en modo estricto pero los navegadores lo toleran.
            data = json.loads(script.string or "", strict=False)
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("@type") == "JobPosting":
            return BeautifulSoup(data.get("description", ""), "html.parser")
    return None


def _extraer_bne(soup: BeautifulSoup) -> str:
    desc_soup = _bne_descripcion_soup(soup)
    return desc_soup.get_text("\n", strip=True) if desc_soup else ""


def _titulo_bne(soup: BeautifulSoup) -> str:
    desc_soup = _bne_descripcion_soup(soup)
    h1 = desc_soup.select_one("h1") if desc_soup else None
    return h1.get_text(strip=True) if h1 else ""


TRABAJANDO_API_OFERTA = "https://www.trabajando.cl/api/ofertas/{id_oferta}"


def extraer_id_trabajando(url: str) -> str | None:
    m = re.search(r"/trabajo/(\d+)", url)
    return m.group(1) if m else None


def extraer_trabajando(id_oferta: str) -> tuple[str, str]:
    """Trabajando.cl es una SPA — el HTML estático no trae la descripción
    completa, pero su API de detalle sí (a diferencia de /api/searchjob,
    que solo da un snippet corto con el keyword resaltado). No encaja en
    el patrón EXTRACTORES (soup -> str) porque no es HTML: es un fetch a
    una API distinta, keyed por id, que además ya trae el título."""
    resp = requests.get(TRABAJANDO_API_OFERTA.format(id_oferta=id_oferta), headers=HEADERS, timeout=25)
    resp.raise_for_status()
    data = resp.json()
    titulo = data.get("nombreCargo", "")
    descripcion_html = data.get("descripcionOferta", "") or ""
    descripcion = BeautifulSoup(descripcion_html, "html.parser").get_text("\n", strip=True)
    return titulo, descripcion


EXTRACTORES = {
    "getonbrd.com": _extraer_getonbrd,
    "computrabajo.com": _extraer_computrabajo,
    "linkedin.com": _extraer_linkedin,
    "chiletrabajos.cl": _extraer_chiletrabajos,
    "elempleo.com": _extraer_elempleo,
    "magneto365.com": _extraer_magneto,
    "bne.cl": _extraer_bne,
}

# Solo para sitios cuya página de detalle no usa <h1> para el título
# (el fallback genérico en extraer() ya cubre el resto).
TITULOS = {
    "magneto365.com": _titulo_magneto,
    "bne.cl": _titulo_bne,
}


def extraer(url: str) -> None:
    dominio = urlparse(url).netloc.replace("www.", "").replace("cl.", "")

    if "trabajando.cl" in dominio:
        id_oferta = extraer_id_trabajando(url)
        if not id_oferta:
            print(f"No se pudo sacar el id de oferta del link: {url}")
            return
        titulo, descripcion = extraer_trabajando(id_oferta)
        print(f"TÍTULO: {titulo or '(sin título detectado)'}")
        print(f"LINK: {url}")
        print("-" * 60)
        print(descripcion if descripcion else "(no se pudo extraer la descripción — revisar selector)")
        return

    clave_match = None
    for clave in EXTRACTORES:
        if clave in dominio:
            clave_match = clave
            break

    if clave_match is None:
        print(f"Sitio no soportado para extracción automática: {dominio}")
        print("Laborum no lo necesita (ver docstring del archivo).")
        return

    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    if clave_match in TITULOS:
        titulo = TITULOS[clave_match](soup) or "(sin título detectado)"
    else:
        titulo_el = soup.select_one("h1")
        titulo = titulo_el.get_text(strip=True) if titulo_el else "(sin título detectado)"
    descripcion = EXTRACTORES[clave_match](soup)

    print(f"TÍTULO: {titulo}")
    print(f"LINK: {url}")
    print("-" * 60)
    print(descripcion if descripcion else "(no se pudo extraer la descripción — revisar selector)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python extraer_oferta.py \"<link-de-la-oferta>\"")
        sys.exit(1)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    extraer(sys.argv[1])
