"""
Prueba de IP para Cloud Run: ¿responden los sitios de empleo desde la IP de
salida de Google? Imprime la IP/país de salida, un GET simple a GetOnBrd
(su escaneo completo tarda ~16 min, no vale la pena para esta prueba) y
corre el scanner REAL con 1 keyword sobre el resto de los sitios de Chile.
Compara el resultado con el mismo comando corrido en tu PC:

    python escaner/scan_getonbrd.py --pais Chile --keywords-file kw_prueba.txt --solo computrabajo_cl,laborum_cl,trabajando_cl,chiletrabajos_cl,linkedin,bne_cl
"""
import os
import subprocess
import sys

import requests

sys.path.insert(0, os.getcwd())  # en la imagen el código está en /app (el directorio de trabajo); en el repo, correr desde la raíz
from escaner import scan_getonbrd as scanner  # noqa: E402

SITIOS_PRUEBA = "computrabajo_cl,laborum_cl,trabajando_cl,chiletrabajos_cl,linkedin,bne_cl"
KEYWORD_PRUEBA = "python"


def main():
    try:
        info = requests.get("https://ipinfo.io/json", timeout=15).json()
        print(f"IP de salida: {info.get('ip')} — {info.get('country')} — {info.get('org')}")
    except Exception as e:
        print(f"No se pudo obtener la IP de salida: {e}")

    url = f"{scanner.GETONBRD_BASE_URL}/jobs/{scanner.GETONBRD_CATEGORIES[0]}"
    try:
        resp = requests.get(url, headers=scanner.HEADERS, timeout=20)
        print(f"GetOnBrd (GET simple): HTTP {resp.status_code}, {len(resp.text)} bytes")
    except Exception as e:
        print(f"GetOnBrd (GET simple): ERROR {type(e).__name__}: {e}")

    with open("kw_prueba.txt", "w", encoding="utf-8") as f:
        f.write(KEYWORD_PRUEBA + "\n")
    print(f"\n--- Scan real, keyword '{KEYWORD_PRUEBA}', sitios: {SITIOS_PRUEBA} ---", flush=True)
    resultado = subprocess.run([
        sys.executable, "escaner/scan_getonbrd.py", "--pais", "Chile",
        "--keywords-file", "kw_prueba.txt", "--solo", SITIOS_PRUEBA,
    ])
    sys.exit(resultado.returncode)


if __name__ == "__main__":
    main()
