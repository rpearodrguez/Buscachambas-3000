"""Tests del almacenamiento (disco y bucket, este último con un cliente falso:
no toca la nube) y de su uso desde el scanner.  Correr con:
    python -m unittest discover -s tests
"""
import csv
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import almacen as alm
import scan_getonbrd as scanner

BUCKET_SECRETO = "bucket-secreto-123"
PREFIJO_SECRETO = "users/abc"


class NotFound(Exception):  # mismo nombre que la excepción real de google-cloud
    pass


class FakeBlob:
    def __init__(self, store, clave, fallar):
        self.store, self.clave, self.fallar = store, clave, fallar

    def download_as_bytes(self):
        if self.fallar:
            raise RuntimeError(f"boom en {self.clave}")
        if self.clave not in self.store:
            raise NotFound()
        return self.store[self.clave]

    def upload_from_string(self, datos):
        if self.fallar:
            raise RuntimeError(f"boom en {self.clave}")
        self.store[self.clave] = datos

    def exists(self):
        if self.fallar:
            raise RuntimeError(f"boom en {self.clave}")
        return self.clave in self.store

    def delete(self):
        if self.clave not in self.store:
            raise NotFound()
        del self.store[self.clave]


class FakeIam:
    def __init__(self, pap, ubla):
        self.public_access_prevention = pap
        self.uniform_bucket_level_access_enabled = ubla


class FakeBucket:
    def __init__(self, store, pap="enforced", ubla=True, fallar=False, reload_falla=False):
        self.store, self.fallar, self.reload_falla = store, fallar, reload_falla
        self.iam_configuration = FakeIam(pap, ubla)

    def blob(self, clave):
        return FakeBlob(self.store, clave, self.fallar)

    def reload(self):
        if self.reload_falla:
            raise PermissionError("403 sin storage.buckets.get")


class FakeClient:
    def __init__(self, **kw):
        self.store = {}
        self.kw = kw

    def bucket(self, nombre):
        return FakeBucket(self.store, **self.kw)


def gcs(prefijo=PREFIJO_SECRETO, **kw):
    a = alm.AlmacenGCS(BUCKET_SECRETO, prefijo, cliente=FakeClient(**kw))
    a.INTENTOS = 1  # sin esperas en los tests
    return a


class AlmacenLocalTests(unittest.TestCase):
    def test_ida_y_vuelta_existe_y_borrar(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            self.assertIsNone(a.leer("x.json"))
            self.assertFalse(a.existe("x.json"))
            a.escribir_texto("x.json", "áé")
            self.assertEqual(a.leer_texto("x.json"), "áé")
            self.assertTrue(a.existe("x.json"))
            self.assertFalse(os.path.exists(os.path.join(d, "x.json.tmp")))
            a.borrar("x.json")
            a.borrar("x.json")  # borrar uno inexistente no falla
            self.assertFalse(a.existe("x.json"))


class AlmacenGCSTests(unittest.TestCase):
    def test_ida_y_vuelta_y_faltante(self):
        a = gcs()
        self.assertIsNone(a.leer("k.txt"))
        a.escribir_texto("k.txt", "hola")
        self.assertEqual(a.leer_texto("k.txt"), "hola")
        self.assertTrue(a.existe("k.txt"))
        a.borrar("k.txt")
        a.borrar("k.txt")
        self.assertFalse(a.existe("k.txt"))

    def test_carpetas_de_usuarios_aisladas(self):
        cliente = FakeClient()
        a = alm.AlmacenGCS("b", "users/aaa", cliente=cliente)
        b = alm.AlmacenGCS("b", "users/bbb", cliente=cliente)
        a.escribir_texto("scan_status.json", "de A")
        self.assertIsNone(b.leer("scan_status.json"))
        self.assertEqual(list(cliente.store), ["users/aaa/scan_status.json"])

    def test_descripcion_no_revela_bucket_ni_prefijo(self):
        d = gcs().descripcion("scan_status.json")
        self.assertNotIn(BUCKET_SECRETO, d)
        self.assertNotIn(PREFIJO_SECRETO, d)
        self.assertNotIn("gs://", d)

    def test_errores_hacia_arriba_son_genericos(self):
        a = gcs(fallar=True)
        for operacion in (lambda: a.leer("k"), lambda: a.escribir("k", b"x"), lambda: a.existe("k")):
            with self.assertRaises(alm.ErrorAlmacen) as ctx:
                operacion()
            for secreto in (BUCKET_SECRETO, PREFIJO_SECRETO, "boom"):
                self.assertNotIn(secreto, str(ctx.exception))

    def test_bucket_privado_pasa(self):
        gcs(pap="enforced", ubla=True).verificar_privado()

    def test_bucket_sin_prevencion_de_acceso_publico_se_rechaza(self):
        for pap in ("inherited", None):
            with self.assertRaises(alm.ErrorAlmacen):
                gcs(pap=pap, ubla=True).verificar_privado()

    def test_bucket_sin_acceso_uniforme_se_rechaza(self):
        with self.assertRaises(alm.ErrorAlmacen):
            gcs(pap="enforced", ubla=False).verificar_privado()

    def test_bucket_no_verificable_se_rechaza(self):
        with self.assertRaises(alm.ErrorAlmacen) as ctx:
            gcs(reload_falla=True).verificar_privado()
        self.assertNotIn(BUCKET_SECRETO, str(ctx.exception))

    def test_crear_almacen(self):
        self.assertIsInstance(alm.crear_almacen(None), alm.AlmacenLocal)
        self.assertIsInstance(alm.crear_almacen(""), alm.AlmacenLocal)
        for malo in ("gs://", "s3://x", "/tmp/algo"):
            with self.assertRaises(ValueError):
                alm.crear_almacen(malo, verificar_privado=False)


class UsuariosTests(unittest.TestCase):
    def test_id_estable_opaco_y_normalizado(self):
        a = alm.id_usuario("Ana@Correo.com ")
        self.assertEqual(a, alm.id_usuario("ana@correo.com"))
        self.assertNotEqual(a, alm.id_usuario("otra@correo.com"))
        self.assertNotIn("ana", a)
        self.assertEqual(len(a), 16)

    def test_destino_de_usuario(self):
        self.assertIsNone(alm.destino_de_usuario(None, None))
        self.assertEqual(alm.destino_de_usuario("gs://b/", "abc"), "gs://b/users/abc")
        with self.assertRaises(ValueError):
            alm.destino_de_usuario("gs://b", "")


class ScannerSobreAlmacenTests(unittest.TestCase):
    FILA = {k: "" for k in scanner.CSV_FIELDS}
    FILA.update(sitio="X", titulo="Dev", empresa="E", link="http://a", activa="SI", fecha_creacion="2026-01-01")

    def test_guardar_resultados_preserva_otros_sitios_y_fechas(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            previa = dict(self.FILA, sitio="Otro", link="http://o")
            vieja = dict(self.FILA, fecha_creacion="2020-02-02")
            scanner.guardar_resultados([previa, vieja], {"X", "Otro"}, a)
            nueva = dict(self.FILA, fecha_creacion="2026-10-25")
            finales = scanner.guardar_resultados([nueva], {"X"}, a)
            self.assertEqual({f["sitio"] for f in finales}, {"X", "Otro"})
            self.assertEqual([f for f in finales if f["sitio"] == "X"][0]["fecha_creacion"], "2020-02-02")
            crudo = a.leer(scanner.OUTPUT_CSV)
            self.assertIn(b"\r\n", crudo)  # mismo formato que antes (csv con newline="")
            self.assertEqual(len(list(csv.DictReader(io.StringIO(crudo.decode("utf-8"))))), 2)

    def test_escritor_espacia_las_subidas_pero_fuerza_las_finales(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            e = scanner.EscritorEstado(a, "s.json", "d.flag", "Chile", ["X"], min_intervalo_guardado=60)
            for i in range(50):
                e.on_paso("X", f"k{i}", i, 50)
            en_disco = json.loads(a.leer_texto("s.json"))
            self.assertEqual(en_disco["pasos_completados"], 0)  # las 50 subidas se espaciaron
            e.finalizar([], [])
            en_disco = json.loads(a.leer_texto("s.json"))
            self.assertEqual(en_disco["pasos_completados"], 50)
            self.assertFalse(en_disco["corriendo"])
            self.assertIn("duracion_seg", en_disco["resultado"])

    def test_escritor_sin_espaciado_guarda_en_cada_paso(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            e = scanner.EscritorEstado(a, "s.json", "d.flag", "Chile", ["X"])
            e.on_paso("X", "k", 1, 5)
            self.assertEqual(json.loads(a.leer_texto("s.json"))["pasos_completados"], 1)

    def test_detener_flag_con_cache_y_borrado_al_finalizar(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            e = scanner.EscritorEstado(a, "s.json", "d.flag", "Chile", ["X"], intervalo_detener=60)
            self.assertFalse(e.debe_detener())
            a.escribir("d.flag", b"")
            self.assertFalse(e.debe_detener())  # dentro del intervalo, no vuelve a consultar
            e._ultimo_chequeo_detener = (0.0, False)
            self.assertTrue(e.debe_detener())
            e.finalizar([], [])
            self.assertFalse(a.existe("d.flag"))

    def test_falla_del_almacen_no_tumba_el_scan(self):
        e = scanner.EscritorEstado(alm.AlmacenLocal(tempfile.gettempdir()), "s.json", None, "Chile", ["X"])
        e.almacen = gcs(fallar=True)
        e._guardar(forzar=True)  # no lanza

    def test_correo_adjunta_bytes(self):
        enviados = []

        class Fake:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def starttls(self):
                pass

            def login(self, *a):
                pass

            def send_message(self, m):
                enviados.append(m)

        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "c.json")
            with open(cfg, "w") as f:
                json.dump({"host": "h", "port": 587, "usuario": "u@x", "password": "p"}, f)
            viejo = (scanner.EMAIL_CONFIG_FILE, scanner.smtplib.SMTP)
            scanner.EMAIL_CONFIG_FILE, scanner.smtplib.SMTP = cfg, Fake
            try:
                self.assertIsNone(scanner.enviar_correo_fin("a@b.c", "t", "c", adjuntos=[("o.csv", "té,1\n".encode("utf-8"))]))
            finally:
                scanner.EMAIL_CONFIG_FILE, scanner.smtplib.SMTP = viejo
        adj = list(enviados[0].iter_attachments())[0]
        self.assertEqual(adj.get_filename(), "o.csv")
        self.assertTrue(adj.get_payload(decode=True).startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
