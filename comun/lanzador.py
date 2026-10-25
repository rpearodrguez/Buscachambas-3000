"""
Lanzar el scan como Cloud Run Job y detectar uno que dejó de dar señales
------------------------------------------------------------------------
En local, la GUI lanza el scan con subprocess.Popen. En la nube lo lanza
como una ejecución de un Cloud Run Job (el scan corre en otra máquina, sin
depender de que la GUI siga viva). El nombre del job y las credenciales
vienen del entorno / secrets de la plataforma, nunca del repo.

google-cloud-run se importa recién al lanzar, así que el modo local no lo
necesita (ver requirements-cloud.txt).
"""

import re
import sys
import time

# El scan escribe su estado cada ~10 s en la nube, y un paso puede tardar
# hasta ~25 s (timeout de una request). Pasado este tiempo sin ninguna
# escritura, se da por muerto.
UMBRAL_SIN_LATIDO = 180

_FORMATO_JOB = re.compile(r"^projects/[a-z0-9-]+/locations/[a-z0-9-]+/jobs/[a-z0-9-]+$")


class ErrorLanzador(Exception):
    """Mensaje genérico a propósito: llega a la pantalla del usuario y no
    debe revelar detalles de la infraestructura (esos van a stderr)."""


def scan_sin_latido(estado: dict | None, ahora: float | None = None, umbral: float = UMBRAL_SIN_LATIDO) -> bool:
    """True si el estado dice "corriendo" pero hace más de `umbral` segundos
    que nadie lo actualiza (el job murió, se pasó del timeout, etc.)."""
    if not estado or not estado.get("corriendo"):
        return False
    ultimo = estado.get("actualizado_ts") or estado.get("inicio_ts")
    if not ultimo:
        return False
    ahora = time.time() if ahora is None else ahora
    return ahora - ultimo > umbral


def lanzar_job(nombre_job: str, argumentos: list[str], credenciales=None) -> None:
    """Ejecuta el job con `argumentos` (los de la CLI de escaner/scan_getonbrd.py)
    sin esperar a que termine. nombre_job: projects/<p>/locations/<r>/jobs/<j>."""
    if not _FORMATO_JOB.match(nombre_job or ""):
        raise ErrorLanzador("La configuración del job de escaneo no es válida.")
    try:
        from google.cloud import run_v2

        cliente = run_v2.JobsClient(credentials=credenciales) if credenciales is not None else run_v2.JobsClient()
        overrides = run_v2.RunJobRequest.Overrides(
            container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(args=argumentos)]
        )
        cliente.run_job(request=run_v2.RunJobRequest(name=nombre_job, overrides=overrides))
    except Exception as e:
        print(f"[lanzador] No se pudo lanzar el job: {type(e).__name__}: {e}", file=sys.stderr)
        raise ErrorLanzador("No se pudo iniciar el escaneo (ver el log del servidor).") from None
