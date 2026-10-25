"""Tests de la fase 4: aviso por correo al administrador cuando un escaneo falla, con el log adjunto.
    python -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from comun import almacen as alm
from escaner import scan_getonbrd as scanner


class FalsoSMTP:
    enviados = []
    falla = False

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def starttls(self):
        pass

    def login(self, *a):
        if FalsoSMTP.falla:
            raise OSError("sin conexion")

    def send_message(self, m):
        FalsoSMTP.enviados.append(m)


class RegistroTests(unittest.TestCase):
    def test_copia_stdout_y_stderr_en_orden_y_no_altera_lo_que_se_imprime(self):
        salida, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(error):
            registro = scanner.RegistroDeEjecucion().instalar()
            print("uno")
            print("dos", file=sys.stderr)
            print("tres")
            registro.restaurar()
        self.assertEqual(registro.contenido(), "uno\ndos\ntres\n")
        self.assertEqual(salida.getvalue(), "uno\ntres\n")  # el destino original recibe lo suyo
        self.assertEqual(error.getvalue(), "dos\n")

    def test_restaurar_devuelve_los_streams_originales(self):
        antes = (sys.stdout, sys.stderr)
        registro = scanner.RegistroDeEjecucion().instalar()
        self.assertIsNot(sys.stdout, antes[0])
        registro.restaurar()
        self.assertEqual((sys.stdout, sys.stderr), antes)

    def test_el_tope_conserva_el_final_donde_esta_el_error(self):
        registro = scanner.RegistroDeEjecucion()
        registro.TOPE = 100
        for i in range(50):
            registro.escribir(f"linea {i:03d}\n")
        texto = registro.contenido()
        self.assertIn("linea 049", texto)
        self.assertNotIn("linea 000", texto)
        self.assertIn("recortó", texto)
        self.assertLessEqual(len(texto), 100 + 80)

    def test_sin_exceso_no_dice_que_recorto(self):
        registro = scanner.RegistroDeEjecucion()
        registro.escribir("poco\n")
        self.assertEqual(registro.contenido(), "poco\n")

    def test_un_atributo_del_stream_original_sigue_disponible(self):
        registro = scanner.RegistroDeEjecucion().instalar()
        try:
            self.assertTrue(hasattr(sys.stdout, "isatty"))
        finally:
            registro.restaurar()


class ArmarAlertaTests(unittest.TestCase):
    def test_trae_quien_que_y_la_traza(self):
        asunto, cuerpo = scanner.armar_alerta(
            {"usuario": "ana@x.cl", "pais": "Chile", "sitios": "laborum_cl", "n_keywords": 9}, "RuntimeError: boom", "Traceback...\nRuntimeError: boom")
        self.assertIn("ana@x.cl", asunto)
        for esperado in ("ana@x.cl", "Chile", "laborum_cl", "Keywords: 9", "RuntimeError: boom", "Traceback...", "log_escaneo.txt", "UTC"):
            self.assertIn(esperado, cuerpo)

    def test_sin_usuario_ni_traza(self):
        asunto, cuerpo = scanner.armar_alerta({}, "motivo", None)
        self.assertIn("ejecución manual", asunto)
        self.assertNotIn("Traza del error", cuerpo)
        self.assertIn("todos los del país", cuerpo)


class Base(unittest.TestCase):
    ADMIN = "admin@x.cl"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = os.path.join(self.tmp.name, "smtp.json")
        with open(cfg, "w") as f:
            json.dump({"host": "h", "port": 587, "usuario": "u@x", "password": "p"}, f)
        with open(os.path.join(self.tmp.name, "kw.txt"), "w") as f:
            f.write("python\nbackup\n")
        self._viejo = (scanner.EMAIL_CONFIG_FILE, scanner.smtplib.SMTP, scanner.ejecutar_sitios, scanner.crear_almacen,
                       os.environ.get("BUSCACHAMBAS_SMTP_HOST"), os.environ.get("BUSCACHAMBAS_ADMIN_EMAIL"), sys.argv[:])
        os.environ.pop("BUSCACHAMBAS_SMTP_HOST", None)
        os.environ["BUSCACHAMBAS_ADMIN_EMAIL"] = self.ADMIN
        scanner.EMAIL_CONFIG_FILE, scanner.smtplib.SMTP = cfg, FalsoSMTP
        FalsoSMTP.enviados, FalsoSMTP.falla = [], False

    def tearDown(self):
        (scanner.EMAIL_CONFIG_FILE, scanner.smtplib.SMTP, scanner.ejecutar_sitios, scanner.crear_almacen, smtp_host, admin, sys.argv[:]) = self._viejo
        for nombre, valor in (("BUSCACHAMBAS_SMTP_HOST", smtp_host), ("BUSCACHAMBAS_ADMIN_EMAIL", admin)):
            if valor is None:
                os.environ.pop(nombre, None)
            else:
                os.environ[nombre] = valor
        self.tmp.cleanup()

    def correr(self, *argumentos):
        previo = os.getcwd()
        os.chdir(self.tmp.name)
        sys.argv = ["scan_getonbrd.py", *argumentos]
        salida = io.StringIO()
        excepcion, codigo = None, 0
        try:
            with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
                try:
                    scanner.main()
                except SystemExit as e:
                    codigo = e.code
                except Exception as e:  # noqa: BLE001 (lo que se prueba es justo que se propague)
                    excepcion = e
        finally:
            os.chdir(previo)
        return codigo, excepcion, salida.getvalue()

    def estado(self):
        with open(os.path.join(self.tmp.name, "scan_status.json"), encoding="utf-8") as f:
            return json.load(f)

    ARGS = ("--pais", "Chile", "--solo", "laborum_cl", "--keywords-file", "kw.txt", "--status-file", "scan_status.json", "--usuario", "ana@x.cl")


class FalloDelEscaneoTests(Base):
    def romper(self, *a, **k):
        print("[10:00:00] Laborum...")
        raise RuntimeError("boom interno")

    def test_una_excepcion_avisa_al_administrador_con_el_log_adjunto_y_se_sigue_propagando(self):
        scanner.ejecutar_sitios = self.romper
        codigo, excepcion, _ = self.correr(*self.ARGS)
        self.assertIsInstance(excepcion, RuntimeError)  # el job sigue terminando en error
        self.assertEqual(len(FalsoSMTP.enviados), 1)
        m = FalsoSMTP.enviados[0]
        self.assertEqual(m["To"], self.ADMIN)
        self.assertIn("ana@x.cl", m["Subject"])
        cuerpo = m.get_body().get_content()
        for esperado in ("RuntimeError: boom interno", "Traceback", "Usuario: ana@x.cl", "Keywords: 2"):
            self.assertIn(esperado, cuerpo)
        adjunto = list(m.iter_attachments())[0]
        self.assertEqual(adjunto.get_filename(), "log_escaneo.txt")
        log = adjunto.get_payload(decode=True).decode("utf-8")
        self.assertIn("Iniciando escaneo", log)  # lo impreso antes de caerse
        self.assertIn("[10:00:00] Laborum...", log)

    def test_el_estado_queda_cerrado_como_fallido_sin_filtrar_la_traza_al_usuario(self):
        scanner.ejecutar_sitios = self.romper
        self.correr(*self.ARGS)
        e = self.estado()
        self.assertFalse(e["corriendo"])
        self.assertEqual(e["fallo"], {"mensaje": "El escaneo terminó con un error inesperado.", "alertado": True})
        self.assertNotIn("boom interno", json.dumps(e))  # el detalle técnico solo va al administrador
        self.assertNotIn("Traceback", json.dumps(e))

    def test_sin_correo_de_administrador_no_manda_nada_y_lo_anota(self):
        os.environ.pop("BUSCACHAMBAS_ADMIN_EMAIL")
        scanner.ejecutar_sitios = self.romper
        _, excepcion, salida = self.correr(*self.ARGS)
        self.assertIsInstance(excepcion, RuntimeError)
        self.assertEqual(FalsoSMTP.enviados, [])
        self.assertIn("No se pudo avisar al administrador", salida)
        self.assertFalse(self.estado()["fallo"]["alertado"])

    def test_si_el_aviso_mismo_falla_el_error_original_sale_igual(self):
        FalsoSMTP.falla = True
        scanner.ejecutar_sitios = self.romper
        _, excepcion, salida = self.correr(*self.ARGS)
        self.assertIsInstance(excepcion, RuntimeError)
        self.assertEqual(str(excepcion), "boom interno")  # no lo tapó el fallo del correo
        self.assertIn("No se pudo avisar al administrador", salida)
        self.assertFalse(self.estado()["fallo"]["alertado"])

    def test_un_escaneo_normal_no_manda_ninguna_alerta(self):
        scanner.ejecutar_sitios = lambda *a, **k: ([], set())
        codigo, excepcion, salida = self.correr(*self.ARGS)
        self.assertIsNone(excepcion)
        self.assertEqual(codigo, 0)
        self.assertIn("Listo en", salida)
        self.assertEqual(FalsoSMTP.enviados, [])
        self.assertNotIn("fallo", self.estado())

    def test_el_registro_no_queda_instalado_despues_de_correr(self):
        antes = sys.stdout
        scanner.ejecutar_sitios = self.romper
        self.correr(*self.ARGS)
        self.assertIs(sys.stdout, antes)

    def test_sin_estado_en_archivo_igual_avisa(self):
        scanner.ejecutar_sitios = self.romper
        _, excepcion, _ = self.correr("--pais", "Chile", "--solo", "laborum_cl", "--keywords-file", "kw.txt", "--usuario", "ana@x.cl")
        self.assertIsInstance(excepcion, RuntimeError)
        self.assertEqual(len(FalsoSMTP.enviados), 1)  # aunque no haya archivo de estado donde marcar el fallo


class OtrasSalidasTests(Base):
    def test_si_no_se_puede_usar_el_almacenamiento_tambien_avisa(self):
        def malo(destino):
            raise scanner.ErrorAlmacen("sin acceso")
        scanner.crear_almacen = malo
        codigo, _, _ = self.correr(*self.ARGS, "--storage", "gs://b/users/x")
        self.assertEqual(codigo, 1)
        self.assertEqual(len(FalsoSMTP.enviados), 1)
        self.assertIn("almacenamiento", FalsoSMTP.enviados[0].get_body().get_content())

    def test_un_pais_invalido_es_un_error_del_usuario_y_no_alerta(self):
        codigo, _, _ = self.correr("--pais", "Atlantida", "--keywords-file", "kw.txt")
        self.assertEqual(codigo, 1)
        self.assertEqual(FalsoSMTP.enviados, [])

    def test_probar_alerta_manda_un_ejemplo_con_log(self):
        codigo, _, salida = self.correr("--probar-alerta")
        self.assertEqual(codigo, 0)
        self.assertIn("Alerta de ejemplo enviada", salida)
        m = FalsoSMTP.enviados[0]
        self.assertEqual(m["To"], self.ADMIN)
        self.assertIn("ejemplo", m["Subject"])
        self.assertIn("log de ejemplo", list(m.iter_attachments())[0].get_payload(decode=True).decode("utf-8"))

    def test_probar_alerta_sin_correo_de_administrador_falla_con_claridad(self):
        os.environ.pop("BUSCACHAMBAS_ADMIN_EMAIL")
        codigo, _, salida = self.correr("--probar-alerta")
        self.assertEqual(codigo, 1)
        self.assertIn("administrador", salida)
        self.assertEqual(FalsoSMTP.enviados, [])


if __name__ == "__main__":
    unittest.main()
