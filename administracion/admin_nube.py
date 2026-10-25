"""
Operaciones de administración sobre la nube (las usa administracion/admin.py, que corre SOLO
en la PC del administrador, con sus propias credenciales de Google)
------------------------------------------------------------------------------
- configuración local de conexión (admin_local.json, gitignored: ahí van el
  proyecto, la región, el job, el bucket; nada de eso vive en el repo)
- cambiar el servidor/puerto/usuario SMTP del job y el token en Secret Manager
- mandar un correo de prueba ejecutando el job

Las librerías de Google se importan al usarlas (requirements-admin.txt).
"""

import json
import os
import re

CONFIG_LOCAL = "admin_local.json"

CAMPOS = ("project", "region", "job", "bucket", "secret")
_FORMATOS = {
    "project": re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$"),
    "region": re.compile(r"^[a-z]+-[a-z0-9]+$"),
    "job": re.compile(r"^[a-z][a-z0-9-]{0,62}$"),
    "bucket": re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$"),
    "secret": re.compile(r"^[A-Za-z0-9_-]{1,255}$"),
}

# Opcional: tabla de la exportación de facturación a BigQuery (pestaña "Gastos").
_FORMATO_TABLA_BILLING = re.compile(r"^[A-Za-z0-9-]{4,30}\.[A-Za-z0-9_]{1,1024}\.[A-Za-z0-9_]{1,1024}$")

ENV_HOST = "BUSCACHAMBAS_SMTP_HOST"
ENV_PORT = "BUSCACHAMBAS_SMTP_PORT"
ENV_USER = "BUSCACHAMBAS_SMTP_USER"
VERSIONES_A_CONSERVAR = 2  # del secreto: la nueva y la anterior (por si el token nuevo falla)


class ErrorAdmin(ValueError):
    """Entrada inválida; el mensaje se puede mostrar tal cual al administrador."""


# --- configuración local ---------------------------------------------------

def validar_config(cfg: dict) -> dict:
    limpio = {}
    for campo in CAMPOS:
        valor = (cfg.get(campo) or "").strip()
        if not _FORMATOS[campo].match(valor):
            raise ErrorAdmin(f"El campo '{campo}' falta o tiene un formato inválido.")
        limpio[campo] = valor
    tabla = (cfg.get("billing_tabla") or "").strip()
    if tabla:
        if not _FORMATO_TABLA_BILLING.match(tabla):
            raise ErrorAdmin("La tabla de facturación debe verse como proyecto.dataset.tabla.")
        limpio["billing_tabla"] = tabla
    return limpio


def cargar_config_local(ruta: str = CONFIG_LOCAL) -> dict | None:
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            return validar_config(json.load(f))
    except (json.JSONDecodeError, OSError, ErrorAdmin):
        return None


def guardar_config_local(cfg: dict, ruta: str = CONFIG_LOCAL) -> dict:
    limpio = validar_config(cfg)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(limpio, f, indent=2)
    return limpio


def credenciales_admin(cfg: dict):
    """Credenciales de aplicación de tu cuenta de Google (gcloud auth application-default login),
    con el proyecto de la conexión como proyecto: así no se depende de que gcloud tenga uno configurado."""
    import google.auth

    credenciales, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    try:
        credenciales.project_id = cfg["project"]  # lo lee AlmacenGCS
    except AttributeError:
        pass  # credenciales de cuenta de servicio: ya traen su propio project_id
    return credenciales


def nombre_job(cfg: dict) -> str:
    return f"projects/{cfg['project']}/locations/{cfg['region']}/jobs/{cfg['job']}"


def destino_admin(cfg: dict) -> str:
    """Parte "admin" del bucket (usuarios, etc.), fuera de las carpetas de usuario."""
    return f"gs://{cfg['bucket']}/admin"


# --- validación de lo que se escribe en el formulario de correo -------------

def validar_smtp(host: str, puerto, usuario: str) -> tuple[str, int, str]:
    host = (host or "").strip().lower()
    if not re.match(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$", host) or len(host) > 253:
        raise ErrorAdmin("El servidor SMTP no es válido (solo un nombre de host, sin esquema ni puerto).")
    try:
        puerto = int(puerto)
    except (TypeError, ValueError):
        raise ErrorAdmin("El puerto debe ser un número.") from None
    if not 1 <= puerto <= 65535:
        raise ErrorAdmin("El puerto debe estar entre 1 y 65535.")
    usuario = (usuario or "").strip()
    if not usuario or len(usuario) > 254 or re.search(r"\s", usuario):
        raise ErrorAdmin("El usuario SMTP falta o tiene espacios.")
    return host, puerto, usuario


# --- operaciones en la nube --------------------------------------------------

def leer_smtp_del_job(cfg: dict, credenciales=None) -> dict:
    """host/puerto/usuario actuales (variables de entorno del job). La
    contraseña nunca se lee: vive en Secret Manager y solo se puede reemplazar."""
    from google.cloud import run_v2

    cliente = run_v2.JobsClient(credentials=credenciales) if credenciales is not None else run_v2.JobsClient()
    job = cliente.get_job(name=nombre_job(cfg))
    env = {e.name: e.value for e in job.template.template.containers[0].env}
    return {"host": env.get(ENV_HOST, ""), "port": env.get(ENV_PORT, "587"), "usuario": env.get(ENV_USER, "")}


def actualizar_smtp_job(cfg: dict, host: str, puerto: int, usuario: str, credenciales=None) -> None:
    """Escribe host/puerto/usuario en las variables de entorno del job
    (crea una revisión nueva; las demás variables y secretos no se tocan)."""
    from google.cloud import run_v2

    cliente = run_v2.JobsClient(credentials=credenciales) if credenciales is not None else run_v2.JobsClient()
    job = cliente.get_job(name=nombre_job(cfg))
    contenedor = job.template.template.containers[0]
    for nombre, valor in ((ENV_HOST, host), (ENV_PORT, str(puerto)), (ENV_USER, usuario)):
        for e in contenedor.env:
            if e.name == nombre:
                e.value = valor
                break
        else:
            contenedor.env.append(run_v2.EnvVar(name=nombre, value=valor))
    cliente.update_job(job=job).result(timeout=300)


def reemplazar_token(cfg: dict, token: str, credenciales=None) -> int:
    """Agrega el token SMTP como versión nueva del secreto y destruye las
    versiones viejas (se conservan la nueva y la anterior: las versiones
    activas por encima de 6 se cobran). Devuelve cuántas destruyó. El job
    usa ':latest', así que la próxima ejecución ya toma el token nuevo."""
    if not token or not token.strip():
        raise ErrorAdmin("El token está vacío.")
    from google.cloud import secretmanager

    cliente = secretmanager.SecretManagerServiceClient(credentials=credenciales) if credenciales is not None else secretmanager.SecretManagerServiceClient()
    padre = f"projects/{cfg['project']}/secrets/{cfg['secret']}"
    cliente.add_secret_version(request={"parent": padre, "payload": {"data": token.strip().encode("utf-8")}})

    versiones = [v for v in cliente.list_secret_versions(request={"parent": padre})
                 if v.state.name in ("ENABLED", "DISABLED")]
    versiones.sort(key=lambda v: v.create_time, reverse=True)
    destruidas = 0
    for v in versiones[VERSIONES_A_CONSERVAR:]:
        cliente.destroy_secret_version(request={"name": v.name})
        destruidas += 1
    return destruidas


def probar_correo(cfg: dict, destinatario: str, credenciales=None) -> tuple[bool, str]:
    """Ejecuta el job con --probar-correo y espera a que termine. Prueba el
    camino real: el SMTP y el token tal como los ve el job."""
    from google.cloud import run_v2

    cliente = run_v2.JobsClient(credentials=credenciales) if credenciales is not None else run_v2.JobsClient()
    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(args=["--probar-correo", destinatario])]
    )
    ejecucion = cliente.run_job(request=run_v2.RunJobRequest(name=nombre_job(cfg), overrides=overrides)).result(timeout=180)
    if ejecucion.succeeded_count >= 1:
        return True, f"Correo de prueba enviado a {destinatario}."
    return False, "El job terminó con error: revisa los logs del job (Cloud Run → Jobs → Logs)."


def consultar_gasto(cfg: dict, dias: int, credenciales=None) -> list[dict]:
    """Costo diario por proyecto y servicio de los últimos `dias` días, desde
    la exportación de facturación a BigQuery (bruto, y créditos aparte: el
    free tier aparece como créditos negativos). Solo lectura. El nombre de la
    tabla se valida con una lista blanca de caracteres antes de ir a la consulta;
    el resto de los valores viajan como parámetros."""
    tabla = cfg.get("billing_tabla")
    if not tabla or not _FORMATO_TABLA_BILLING.match(tabla):
        raise ErrorAdmin("Falta la tabla de la exportación de facturación (pestaña Conexión).")
    dias = int(dias)
    if not 1 <= dias <= 400:
        raise ErrorAdmin("El período debe estar entre 1 y 400 días.")
    from google.cloud import bigquery

    cliente = bigquery.Client(project=cfg["project"], credentials=credenciales)
    consulta = f"""
        SELECT DATE(usage_start_time) AS dia,
               IFNULL(project.id, '(cuenta)') AS proyecto,
               service.description AS servicio,
               IFNULL(sku.description, '(sin SKU)') AS sku,
               IFNULL(location.region, IFNULL(location.location, '-')) AS ubicacion,
               currency AS moneda,
               SUM(cost) AS bruto,
               SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0)) AS creditos
        FROM `{tabla}`
        WHERE usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @dias DAY)
        GROUP BY dia, proyecto, servicio, sku, ubicacion, moneda
        HAVING ABS(bruto) > 0.000001 OR ABS(creditos) > 0.000001
        ORDER BY dia
    """
    trabajo = cliente.query(consulta, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("dias", "INT64", dias)]))
    return [
        {"dia": r["dia"].isoformat(), "proyecto": r["proyecto"], "servicio": r["servicio"],
         "sku": r["sku"], "ubicacion": r["ubicacion"], "moneda": r["moneda"], "bruto": float(r["bruto"] or 0), "creditos": float(r["creditos"] or 0)}
        for r in trabajo.result()
    ]


# --- limpieza automática del bucket (regla de ciclo de vida, la ejecuta Google solo) ---
# Solo para lo efímero: los candados y banderas que a veces quedan colgados. Los datos de los usuarios NUNCA van
# en una regla automática: una regla por "edad" borraría, por ejemplo, unas keywords guardadas hace un año de
# alguien que usa la herramienta todas las semanas. Esa limpieza es manual y con vista previa (pestaña Limpieza).
SUFIJOS_TEMPORALES = ["/lanzando.lock", "/detener.flag"]
DIAS_TEMPORALES = 1


def _bucket(cfg: dict, credenciales=None, cliente=None):
    if cliente is None:
        from google.cloud import storage

        cliente = storage.Client(project=cfg["project"], credentials=credenciales)
    return cliente.get_bucket(cfg["bucket"])


def _es_regla_de_temporales(regla) -> bool:
    condicion = regla.get("condition", {})
    return (
        regla.get("action", {}).get("type") == "Delete"
        and condicion.get("age") == DIAS_TEMPORALES
        and sorted(condicion.get("matchesSuffix", [])) == sorted(SUFIJOS_TEMPORALES)
    )


def regla_de_temporales_activa(cfg: dict, credenciales=None, cliente=None) -> bool:
    """¿Ya está la regla que borra sola los candados y banderas con más de un día?"""
    return any(_es_regla_de_temporales(r) for r in _bucket(cfg, credenciales, cliente).lifecycle_rules)


def activar_regla_de_temporales(cfg: dict, credenciales=None, cliente=None) -> bool:
    """Agrega esa regla al bucket sin tocar las demás. True si la agregó, False si ya estaba (idempotente)."""
    bucket = _bucket(cfg, credenciales, cliente)
    if any(_es_regla_de_temporales(r) for r in bucket.lifecycle_rules):
        return False
    bucket.add_lifecycle_delete_rule(age=DIAS_TEMPORALES, matches_suffix=SUFIJOS_TEMPORALES)
    bucket.patch()
    return True
