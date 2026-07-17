"""
Extractor de descripción completa de una oferta
------------------------------------------------
Dado el link de una oferta (de las que salen en ofertas_empleos.csv),
descarga la página y saca el texto completo y limpio (título, empresa,
descripción) listo para pegar en el Project de Claude que arma
currículums.

Cubre GetOnBrd, Computrabajo (Chile y Colombia), ChileTrabajos, ElEmpleo
y LinkedIn (donde la descripción completa viene en el HTML). Laborum y
Trabajando.cl son SPAs: no traen la descripción completa por request
simple, así que para esos dos hay que copiar el texto directo desde el
navegador.

USO:
    python extraer_oferta.py "<link-de-la-oferta>"
"""

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


EXTRACTORES = {
    "getonbrd.com": _extraer_getonbrd,
    "computrabajo.com": _extraer_computrabajo,
    "linkedin.com": _extraer_linkedin,
    "chiletrabajos.cl": _extraer_chiletrabajos,
    "elempleo.com": _extraer_elempleo,
    "magneto365.com": _extraer_magneto,
}

# Solo para sitios cuya página de detalle no usa <h1> para el título
# (el fallback genérico en extraer() ya cubre el resto).
TITULOS = {
    "magneto365.com": _titulo_magneto,
}


def extraer(url: str) -> None:
    dominio = urlparse(url).netloc.replace("www.", "").replace("cl.", "")
    clave_match = None
    for clave in EXTRACTORES:
        if clave in dominio:
            clave_match = clave
            break

    if clave_match is None:
        print(f"Sitio no soportado para extracción automática: {dominio}")
        print("Laborum y Trabajando.cl son SPAs — copia el texto directo desde el navegador.")
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
