"""Tests de lanzador.py (detección de scans colgados, validación del job) y del
estado inicial que la GUI deja escrito al lanzar un scan en la nube.
    python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lanzador
import scan_getonbrd as scanner


class SinLatidoTests(unittest.TestCase):
    def test_no_corriendo_o_sin_estado_nunca_esta_colgado(self):
        self.assertFalse(lanzador.scan_sin_latido(None))
        self.assertFalse(lanzador.scan_sin_latido({"corriendo": False, "actualizado_ts": 0}, ahora=10_000))

    def test_con_latido_reciente_no_esta_colgado(self):
        estado = {"corriendo": True, "actualizado_ts": 1000.0}
        self.assertFalse(lanzador.scan_sin_latido(estado, ahora=1000 + lanzador.UMBRAL_SIN_LATIDO - 1))

    def test_sin_latido_pasado_el_umbral_esta_colgado(self):
        estado = {"corriendo": True, "actualizado_ts": 1000.0}
        self.assertTrue(lanzador.scan_sin_latido(estado, ahora=1000 + lanzador.UMBRAL_SIN_LATIDO + 1))

    def test_job_que_nunca_arranco_usa_inicio_ts(self):
        estado = {"corriendo": True, "inicio_ts": 1000.0}
        self.assertTrue(lanzador.scan_sin_latido(estado, ahora=1000 + lanzador.UMBRAL_SIN_LATIDO + 1))


class LanzarJobTests(unittest.TestCase):
    def test_nombre_de_job_invalido_se_rechaza_sin_detalles(self):
        for malo in ("", None, "cualquier cosa", "projects/p/locations/r/jobs/", "projects/P;rm/locations/r/jobs/j"):
            with self.assertRaises(lanzador.ErrorLanzador) as ctx:
                lanzador.lanzar_job(malo, ["--pais", "Chile"])
            self.assertNotIn("projects", str(ctx.exception))

    def test_fallo_al_lanzar_es_generico(self):
        # Formato válido pero sin google-cloud-run / credenciales: no debe filtrar el nombre.
        nombre = "projects/proyecto-secreto/locations/region-x/jobs/job-secreto"
        with self.assertRaises(lanzador.ErrorLanzador) as ctx:
            lanzador.lanzar_job(nombre, [])
        for secreto in ("proyecto-secreto", "region-x", "job-secreto"):
            self.assertNotIn(secreto, str(ctx.exception))


class EstadoInicialTests(unittest.TestCase):
    def test_tiene_todo_lo_que_lee_la_gui(self):
        e = scanner.estado_inicial("Chile", ["A", "B"], ahora=123.0, pid=0)
        self.assertTrue(e["corriendo"])
        self.assertEqual((e["pid"], e["inicio_ts"], e["actualizado_ts"]), (0, 123.0, 123.0))
        for clave in ("progreso", "pasos_completados", "pasos_totales_por_sitio", "log", "errores", "ofertas_encontradas", "resultado"):
            self.assertIn(clave, e)
        self.assertEqual(e["progreso"], {"sitio": "", "etiqueta": "", "i": 0, "total": 0})


if __name__ == "__main__":
    unittest.main()
