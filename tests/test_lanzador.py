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


class DetenerConservaLoEncontradoTests(unittest.TestCase):
    """Al detener, cada sitio debe devolver lo que ya había encontrado (antes
    ejecutar_sitios cortaba de golpe y esas ofertas se perdían)."""

    def _sitio_falso(self, nombre, pasos_hasta_ver_stop):
        pedidos = []

        def fn(keywords, require_remote, on_progreso=None, debe_detener=None, on_error=None, on_oferta=None):
            encontradas = []
            for kw in keywords:
                if debe_detener and debe_detener():
                    break
                pedidos.append((nombre, kw))
                encontradas.append({"sitio": nombre, "titulo": kw, "link": f"http://{nombre}/{kw}"})
                yield
            return encontradas

        return fn, pedidos

    def test_detener_a_mitad_devuelve_las_ofertas_ya_encontradas_y_no_hace_mas_requests(self):
        fn_a, pedidos_a = self._sitio_falso("A", 2)
        fn_b, pedidos_b = self._sitio_falso("B", 2)
        viejos = (scanner.SITIOS_POR_ID, scanner.REQUEST_DELAY_SECONDS)
        scanner.SITIOS_POR_ID = {"a": {"id": "a", "nombre": "A", "fn": fn_a}, "b": {"id": "b", "nombre": "B", "fn": fn_b}}
        scanner.REQUEST_DELAY_SECONDS = 0
        try:
            llamadas = {"n": 0}

            def debe_detener():
                llamadas["n"] += 1
                return len(pedidos_a) + len(pedidos_b) >= 4  # tras 2 pedidos por sitio

            filas, habilitados = scanner.ejecutar_sitios(
                ["a", "b"], ["k1", "k2", "k3", "k4", "k5"], True, "Chile", debe_detener=debe_detener,
            )
        finally:
            scanner.SITIOS_POR_ID, scanner.REQUEST_DELAY_SECONDS = viejos
        self.assertEqual(habilitados, {"A", "B"})
        self.assertEqual(sorted(f["titulo"] for f in filas if f["sitio"] == "A"), ["k1", "k2"])
        self.assertEqual(sorted(f["titulo"] for f in filas if f["sitio"] == "B"), ["k1", "k2"])
        self.assertEqual(len(pedidos_a), 2)  # no siguió pidiendo después de detener
        self.assertEqual(len(pedidos_b), 2)

    def test_sin_detener_corre_todo(self):
        fn_a, pedidos_a = self._sitio_falso("A", 0)
        viejos = (scanner.SITIOS_POR_ID, scanner.REQUEST_DELAY_SECONDS)
        scanner.SITIOS_POR_ID = {"a": {"id": "a", "nombre": "A", "fn": fn_a}}
        scanner.REQUEST_DELAY_SECONDS = 0
        try:
            filas, _ = scanner.ejecutar_sitios(["a"], ["k1", "k2", "k3"], True, "Chile", debe_detener=lambda: False)
        finally:
            scanner.SITIOS_POR_ID, scanner.REQUEST_DELAY_SECONDS = viejos
        self.assertEqual(len(filas), 3)


class InicioTests(unittest.TestCase):
    def test_inicio_valido(self):
        self.assertEqual(scanner.inicio_valido(995.0, ahora=1000.0), 995.0)
        self.assertEqual(scanner.inicio_valido(None, ahora=1000.0), 1000.0)
        self.assertEqual(scanner.inicio_valido(1005.0, ahora=1000.0), 1000.0)  # en el futuro: se ignora
        self.assertEqual(scanner.inicio_valido(1.0, ahora=10_000.0), 10_000.0)  # de hace más de una hora: se ignora

    def test_el_escritor_parte_del_inicio_que_le_pasan(self):
        import tempfile
        import almacen as alm
        with tempfile.TemporaryDirectory() as d:
            e = scanner.EscritorEstado(alm.AlmacenLocal(d), "s.json", None, "Chile", ["X"], inicio=12345.0)
            self.assertEqual(e.estado["inicio_ts"], 12345.0)
            self.assertEqual(e.inicio, 12345.0)


if __name__ == "__main__":
    unittest.main()
