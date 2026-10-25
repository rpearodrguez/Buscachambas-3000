"""
Almacenamiento de los archivos compartidos entre la GUI y el scan
------------------------------------------------------------------
La GUI y el scan se coordinan por unos pocos archivos (scan_status.json,
detener.flag, el CSV de resultados, las keywords). En local son archivos en
disco; en la nube (GUI en Streamlit Cloud, scan en un Cloud Run Job) no
comparten disco, así que viven en un bucket de Cloud Storage, con una
carpeta por usuario.

Este módulo esconde esa diferencia: scan_getonbrd.py y gui.py piden
"leer/escribir el archivo X" a un Almacen y no saben si es disco o bucket.

    crear_almacen(None)                          -> disco, rutas tal cual (modo local de siempre)
    crear_almacen("gs://mi-bucket/users/abc123") -> bucket, bajo ese prefijo

google-cloud-storage se importa recién al usar un destino gs://, así que el
modo local no necesita instalarlo (ver requirements-cloud.txt).
"""

import hashlib
import os
import sys
import time

PREFIJO_USUARIOS = "users"


class ErrorAlmacen(Exception):
    """Fallo al usar el almacenamiento. El mensaje es genérico a propósito:
    llega a la pantalla del usuario y no debe revelar nombres de bucket,
    rutas ni detalles de la infraestructura (esos van solo al log del
    servidor, por stderr)."""


class Almacen:
    """Interfaz común. Los nombres son simples ("scan_status.json"), nunca rutas."""

    def leer(self, nombre: str) -> bytes | None:
        raise NotImplementedError

    def escribir(self, nombre: str, datos: bytes) -> None:
        raise NotImplementedError

    def existe(self, nombre: str) -> bool:
        raise NotImplementedError

    def borrar(self, nombre: str) -> None:
        raise NotImplementedError

    def descripcion(self, nombre: str) -> str:
        """Dónde quedó el archivo, para mostrarlo en mensajes."""
        raise NotImplementedError

    def crear_si_no_existe(self, nombre: str, datos: bytes) -> bool:
        """Crea el archivo solo si no existía, de forma atómica. True si lo
        creó, False si ya estaba (sirve de candado entre procesos)."""
        raise NotImplementedError

    def listar(self, prefijo: str = "") -> list[str]:
        """Nombres (relativos a este almacén) de los archivos cuyo nombre
        empieza con `prefijo`."""
        raise NotImplementedError

    def leer_texto(self, nombre: str) -> str | None:
        datos = self.leer(nombre)
        return None if datos is None else datos.decode("utf-8")

    def escribir_texto(self, nombre: str, texto: str) -> None:
        self.escribir(nombre, texto.encode("utf-8"))


class AlmacenLocal(Almacen):
    """Archivos en disco. Con base_dir vacío los nombres se usan como rutas
    tal cual (relativas al cwd), que es el comportamiento de antes."""

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir

    def _ruta(self, nombre: str) -> str:
        return os.path.join(self.base_dir, nombre) if self.base_dir else nombre

    def leer(self, nombre):
        try:
            with open(self._ruta(nombre), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def escribir(self, nombre, datos):
        ruta = self._ruta(nombre)
        tmp = f"{ruta}.tmp"
        with open(tmp, "wb") as f:
            f.write(datos)

        # En Windows, os.replace() puede tirar PermissionError si algo más
        # (la GUI, típicamente) tiene el archivo destino abierto para
        # lectura justo en ese instante — es una condición de carrera
        # transitoria, no un error real. Reintentar con backoff corto.
        for intento in range(5):
            try:
                os.replace(tmp, ruta)
                return
            except PermissionError:
                if intento == 4:
                    raise
                time.sleep(0.05 * (intento + 1))

    def existe(self, nombre):
        return os.path.exists(self._ruta(nombre))

    def borrar(self, nombre):
        try:
            os.remove(self._ruta(nombre))
        except FileNotFoundError:
            pass

    def descripcion(self, nombre):
        return self._ruta(nombre)

    def crear_si_no_existe(self, nombre, datos):
        try:
            with open(self._ruta(nombre), "xb") as f:
                f.write(datos)
            return True
        except FileExistsError:
            return False

    def listar(self, prefijo=""):
        base = self.base_dir or "."
        encontrados = []
        for raiz, _, archivos in os.walk(base):
            for archivo in archivos:
                relativo = os.path.relpath(os.path.join(raiz, archivo), base).replace(os.sep, "/")
                if relativo.startswith(prefijo):
                    encontrados.append(relativo)
        return sorted(encontrados)


class AlmacenGCS(Almacen):
    """Archivos en un bucket de Cloud Storage bajo un prefijo (la carpeta
    del usuario). Las credenciales las resuelve google-cloud-storage solo
    (Application Default Credentials: la cuenta de servicio del Cloud Run
    Job, o el secret de la cuenta de servicio en Streamlit Cloud)."""

    INTENTOS = 3

    def __init__(self, bucket: str, prefijo: str = "", cliente=None, credenciales=None):
        self.bucket_nombre = bucket
        self.prefijo = prefijo.strip("/")
        self._cliente = cliente
        self._credenciales = credenciales
        self._bucket = None

    def _obtener_bucket(self):
        if self._bucket is None:
            if self._cliente is None:
                from google.cloud import storage
                if self._credenciales is not None:
                    self._cliente = storage.Client(credentials=self._credenciales, project=self._credenciales.project_id)
                else:
                    self._cliente = storage.Client()
            self._bucket = self._cliente.bucket(self.bucket_nombre)
        return self._bucket

    def _blob(self, nombre: str):
        clave = f"{self.prefijo}/{nombre}" if self.prefijo else nombre
        return self._obtener_bucket().blob(clave)

    def verificar_privado(self) -> None:
        """Se niega a usar un bucket que no esté bloqueado contra acceso
        público: prevención de acceso público *enforced* (imposibilita
        otorgar permisos a allUsers/allAuthenticatedUsers, aunque alguien
        lo intente después) y acceso uniforme a nivel de bucket (sin ACLs
        por objeto). Si no se puede verificar (falta el permiso
        storage.buckets.get), tampoco se usa: ante la duda, no."""
        try:
            bucket = self._obtener_bucket()
            bucket.reload()
            config = bucket.iam_configuration
            privado = (
                config.public_access_prevention == "enforced"
                and bool(config.uniform_bucket_level_access_enabled)
            )
        except Exception as e:
            print(f"[almacen] No se pudo verificar la configuración del bucket: {type(e).__name__}: {e}", file=sys.stderr)
            raise ErrorAlmacen(
                "No se pudo verificar que el almacenamiento sea privado; por seguridad no se usa. "
                "Revisa el log del servidor."
            ) from None
        if not privado:
            print("[almacen] El bucket NO tiene la prevención de acceso público 'enforced' y/o el acceso uniforme activo.", file=sys.stderr)
            raise ErrorAlmacen(
                "El almacenamiento no está bloqueado contra acceso público; por seguridad no se usa. "
                "Revisa el log del servidor."
            )

    def _con_reintentos(self, operacion, que: str):
        # Un fallo de red puntual no debe tumbar un scan de 40 minutos.
        for intento in range(self.INTENTOS):
            try:
                return operacion()
            except Exception as e:
                if type(e).__name__ in ("NotFound", "PreconditionFailed"):
                    raise  # respuestas esperadas del bucket, no fallos para reintentar
                if intento == self.INTENTOS - 1:
                    # Detalle (con el nombre del bucket) solo al log del
                    # servidor; hacia arriba sale un mensaje genérico.
                    print(f"[almacen] Falló {que}: {type(e).__name__}: {e}", file=sys.stderr)
                    raise ErrorAlmacen(f"No se pudo {que} en el almacenamiento (ver el log del servidor).") from None
                time.sleep(0.5 * (intento + 1))

    def leer(self, nombre):
        try:
            return self._con_reintentos(lambda: self._blob(nombre).download_as_bytes(), "leer un archivo")
        except Exception as e:
            if type(e).__name__ == "NotFound":
                return None
            raise

    def escribir(self, nombre, datos):
        self._con_reintentos(lambda: self._blob(nombre).upload_from_string(datos), "guardar un archivo")

    def existe(self, nombre):
        return self._con_reintentos(lambda: self._blob(nombre).exists(), "consultar un archivo")

    def borrar(self, nombre):
        try:
            self._con_reintentos(lambda: self._blob(nombre).delete(), "borrar un archivo")
        except Exception as e:
            if type(e).__name__ != "NotFound":
                raise

    def descripcion(self, nombre):
        # Nunca el nombre del bucket ni el prefijo: este texto termina en
        # mensajes de la GUI y en logs.
        return f"almacenamiento privado ({nombre})"

    def crear_si_no_existe(self, nombre, datos):
        # if_generation_match=0: el bucket rechaza la escritura si el objeto
        # ya existe, de forma atómica (dos procesos no pueden crearlo a la vez).
        try:
            self._con_reintentos(lambda: self._blob(nombre).upload_from_string(datos, if_generation_match=0), "tomar un candado")
            return True
        except ErrorAlmacen:
            raise
        except Exception as e:
            if type(e).__name__ == "PreconditionFailed":
                return False
            raise

    def listar(self, prefijo=""):
        self._obtener_bucket()
        completo = f"{self.prefijo}/{prefijo}" if self.prefijo else prefijo
        quitar = len(self.prefijo) + 1 if self.prefijo else 0
        nombres = self._con_reintentos(
            lambda: [b.name[quitar:] for b in self._cliente.list_blobs(self.bucket_nombre, prefix=completo)],
            "listar archivos",
        )
        return sorted(nombres)


def crear_almacen(destino: str | None, verificar_privado: bool = True, credenciales=None) -> Almacen:
    """None o "" -> disco local. "gs://bucket[/prefijo]" -> Cloud Storage,
    verificando (una vez) que el bucket sea privado. `credenciales`: de una
    cuenta de servicio (la GUI en Streamlit Cloud las trae de sus secrets);
    sin ellas se usan las del entorno (el Cloud Run Job).
    verificar_privado=False existe solo para los tests con un cliente falso."""
    if not destino:
        return AlmacenLocal()
    if destino.startswith("gs://"):
        bucket, _, prefijo = destino[len("gs://"):].partition("/")
        if not bucket:
            raise ValueError("Destino de almacenamiento inválido (se esperaba gs://bucket/prefijo)")
        almacen = AlmacenGCS(bucket, prefijo, credenciales=credenciales)
        if verificar_privado:
            almacen.verificar_privado()
        return almacen
    raise ValueError("Destino de almacenamiento no soportado (usa gs://bucket/prefijo o déjalo vacío)")


def id_usuario(email: str) -> str:
    """Identificador estable y opaco del usuario para nombrar su carpeta
    (hash del correo: no deja correos en los nombres de objetos)."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def destino_de_usuario(base: str | None, usuario: str | None) -> str | None:
    """Carpeta del usuario dentro del destino base. En local (sin base) no
    hay carpetas por usuario: es la PC de una sola persona. `usuario` ya
    es un id (ver id_usuario), nunca texto que haya escrito el usuario."""
    if not base:
        return None
    if not usuario:
        raise ValueError("Falta el usuario para armar su carpeta en el almacenamiento")
    return f"{base.rstrip('/')}/{PREFIJO_USUARIOS}/{usuario}"
