"""Tests del envío manual del resultado por correo (una sola vez por escaneo).
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

INICIO = 1_760_000_000.5

CSV = "sitio,titulo,empresa,keywords_match,remota,activa,motivo,link,fecha_creacion,salario,descripcion\r\n" \
      "X,Dev,E,python,SI,SI,m,http://a,2026-10-01,,\r\nX,Ops,E,backup,SI,NO,m,http://b,2026-10-01,,\r\n"


def estado(**cambios):
    base = {
        "corriendo": False, "inicio_ts": INICIO, "pais": "Chile", "errores": ["⚠ [BNE] 403"],
        "ofertas_encontradas": [{"sitio": "X", "titulo": "Dev", "empresa": "E", "link": "http://a"}],
        "resultado": {"nuevas": 1, "total": 2, "activas": 1, "detenido": False, "duracion_seg": 125.0},
    }
    base.update(cambios)
    return base


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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = alm.AlmacenLocal(self.tmp.name)
        cfg = os.path.join(self.tmp.name, "smtp.json")
        with open(cfg, "w") as f:
            json.dump({"host": "h", "port": 587, "usuario": "u@x", "password": "p"}, f)
        self._viejo = (scanner.EMAIL_CONFIG_FILE, scanner.smtplib.SMTP, os.environ.get("BUSCACHAMBAS_SMTP_HOST"))
        os.environ.pop("BUSCACHAMBAS_SMTP_HOST", None)
        scanner.EMAIL_CONFIG_FILE, scanner.smtplib.SMTP = cfg, FalsoSMTP
        FalsoSMTP.enviados, FalsoSMTP.falla = [], False

    def tearDown(self):
        scanner.EMAIL_CONFIG_FILE, scanner.smtplib.SMTP = self._viejo[0], self._viejo[1]
        if self._viejo[2] is not None:
            os.environ["BUSCACHAMBAS_SMTP_HOST"] = self._viejo[2]
        self.tmp.cleanup()

    def poner_estado(self, **cambios):
        self.a.escribir_texto("scan_status.json", json.dumps(estado(**cambios)))
        self.a.escribir_texto(scanner.OUTPUT_CSV, CSV)


class ReenviarResultadoTests(Base):
    def test_manda_el_mismo_correo_del_final_con_el_csv_adjunto_y_lo_anota(self):
        self.poner_estado()
        self.assertIsNone(scanner.reenviar_resultado(self.a, "yo@x.cl", INICIO))
        m = FalsoSMTP.enviados[0]
        self.assertEqual(m["To"], "yo@x.cl")
        self.assertIn("1 ofertas nuevas (Chile)", m["Subject"])
        cuerpo = m.get_body().get_content()
        self.assertIn("2 min 05 s", cuerpo)
        self.assertIn("1/2 activas", cuerpo)  # activas / total, leído del CSV
        self.assertIn("http://a", cuerpo)
        adjunto = list(m.iter_attachments())[0]
        self.assertEqual(adjunto.get_filename(), scanner.OUTPUT_CSV)
        self.assertTrue(adjunto.get_payload(decode=True).startswith(b"\xef\xbb\xbf"))
        anotado = json.loads(self.a.leer_texto("scan_status.json"))["correo"]
        self.assertEqual((anotado["enviado"], anotado["manual"]), (True, True))

    def test_un_scan_detenido_se_avisa_como_detenido(self):
        self.poner_estado(resultado={"nuevas": 3, "total": 3, "activas": 3, "detenido": True, "duracion_seg": 10})
        scanner.reenviar_resultado(self.a, "yo@x.cl", INICIO)
        self.assertIn("detenido", FalsoSMTP.enviados[0]["Subject"])

    def test_no_manda_si_el_escaneo_sigue_corriendo_o_no_termino_o_no_existe(self):
        self.poner_estado(corriendo=True)
        self.assertIn("en curso", scanner.reenviar_resultado(self.a, "yo@x.cl", INICIO))
        self.poner_estado(resultado=None)
        self.assertIn("no terminó", scanner.reenviar_resultado(self.a, "yo@x.cl", INICIO))
        self.a.borrar("scan_status.json")
        self.assertIn("no hay un escaneo", scanner.reenviar_resultado(self.a, "yo@x.cl", INICIO))
        self.a.escribir_texto("scan_status.json", "{roto")
        self.assertIn("no hay un escaneo", scanner.reenviar_resultado(self.a, "yo@x.cl", INICIO))
        self.assertEqual(FalsoSMTP.enviados, [])

    def test_no_manda_el_resultado_de_otro_escaneo(self):
        self.poner_estado(inicio_ts=INICIO + 3600)  # se lanzó uno nuevo mientras tanto
        self.assertIn("otro escaneo", scanner.reenviar_resultado(self.a, "yo@x.cl", INICIO))
        self.assertEqual(FalsoSMTP.enviados, [])

    def test_un_fallo_del_correo_se_devuelve_y_queda_anotado_sin_romper_nada(self):
        self.poner_estado()
        FalsoSMTP.falla = True
        error = scanner.reenviar_resultado(self.a, "yo@x.cl", INICIO)
        self.assertIn("sin conexion", error)
        anotado = json.loads(self.a.leer_texto("scan_status.json"))["correo"]
        self.assertFalse(anotado["enviado"])
        self.assertEqual(self.a.leer_texto(scanner.OUTPUT_CSV), CSV)  # el CSV no se toca

    def test_sin_csv_igual_manda_el_resumen(self):
        self.poner_estado()
        self.a.borrar(scanner.OUTPUT_CSV)
        self.assertIsNone(scanner.reenviar_resultado(self.a, "yo@x.cl", INICIO))
        self.assertEqual(list(FalsoSMTP.enviados[0].iter_attachments()), [])


class MarcaDeUnSoloEnvioTests(Base):
    def test_solo_el_primer_pedido_de_cada_escaneo_se_acepta(self):
        self.assertTrue(scanner.crear_marca_correo(self.a, INICIO))
        self.assertFalse(scanner.crear_marca_correo(self.a, INICIO))  # doble clic u otra pestaña
        self.assertFalse(scanner.crear_marca_correo(self.a, INICIO + 0.4))  # mismo segundo: mismo escaneo

    def test_un_escaneo_nuevo_se_puede_volver_a_pedir_y_no_se_acumulan_marcas(self):
        scanner.crear_marca_correo(self.a, INICIO)
        self.assertTrue(scanner.crear_marca_correo(self.a, INICIO + 3600))
        marcas = [n for n in self.a.listar("correo_") if n.endswith(".flag")]
        self.assertEqual(marcas, [scanner.nombre_marca_correo(INICIO + 3600)])

    def test_nombre_de_la_marca(self):
        self.assertEqual(scanner.nombre_marca_correo(1760000000.9), "correo_1760000000.flag")

    def test_la_marca_no_interfiere_con_otros_archivos(self):
        self.a.escribir_texto("correo_electronico.txt", "x")  # empieza igual pero no es una marca
        scanner.crear_marca_correo(self.a, INICIO)
        scanner.crear_marca_correo(self.a, INICIO + 3600)
        self.assertTrue(self.a.existe("correo_electronico.txt"))


class RamaDeLaLineaDeComandosTests(Base):
    def correr(self, *argumentos):
        previo = os.getcwd()
        os.chdir(self.tmp.name)
        sys.argv = ["scan_getonbrd.py", *argumentos]
        salida = io.StringIO()
        try:
            with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(salida):
                try:
                    scanner.main()
                    codigo = 0
                except SystemExit as e:
                    codigo = e.code
        finally:
            os.chdir(previo)
        return codigo, salida.getvalue()

    def test_envia_y_termina_sin_escanear(self):
        self.poner_estado()
        codigo, salida = self.correr("--enviar-resultado", "yo@x.cl", "--inicio-ts", repr(INICIO), "--status-file", "scan_status.json")
        self.assertEqual(codigo, 0)
        self.assertIn("Resultado enviado", salida)
        self.assertEqual(len(FalsoSMTP.enviados), 1)

    def test_si_falla_sale_con_error_y_libera_la_marca_para_poder_reintentar(self):
        self.poner_estado(corriendo=True)
        scanner.crear_marca_correo(self.a, INICIO)
        codigo, _ = self.correr("--enviar-resultado", "yo@x.cl", "--inicio-ts", repr(INICIO), "--status-file", "scan_status.json")
        self.assertEqual(codigo, 1)
        self.assertFalse(self.a.existe(scanner.nombre_marca_correo(INICIO)))

    def test_si_sale_bien_la_marca_se_queda_para_no_repetir(self):
        self.poner_estado()
        scanner.crear_marca_correo(self.a, INICIO)
        self.correr("--enviar-resultado", "yo@x.cl", "--inicio-ts", repr(INICIO), "--status-file", "scan_status.json")
        self.assertTrue(self.a.existe(scanner.nombre_marca_correo(INICIO)))


if __name__ == "__main__":
    unittest.main()
