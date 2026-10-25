"""Tests de administracion/actividad.py: cuántos escaneos se han hecho, por día, semana, mes y año.
    python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from administracion import actividad, actividad_grafico
from comun import almacen as alm


def ts(*args):
    """Hora local (la misma en que se agrupa), como epoch."""
    return datetime(*args).timestamp()


class ClavesTests(unittest.TestCase):
    def test_clave_de_cada_granularidad(self):
        m = datetime(2026, 10, 25, 23, 30)  # domingo
        self.assertEqual(actividad.clave_de(m, "dia"), "2026-10-25")
        self.assertEqual(actividad.clave_de(m, "semana"), "2026-10-19")  # el lunes que abre esa semana
        self.assertEqual(actividad.clave_de(m, "mes"), "2026-10")
        self.assertEqual(actividad.clave_de(m, "anio"), "2026")
        with self.assertRaises(ValueError):
            actividad.clave_de(m, "siglo")

    def test_la_semana_empieza_el_lunes(self):
        self.assertEqual(actividad.clave_de(datetime(2026, 10, 26, 0, 0), "semana"), "2026-10-26")  # lunes
        self.assertEqual(actividad.clave_de(datetime(2026, 10, 25, 23, 59), "semana"), "2026-10-19")  # domingo, semana anterior

    def test_un_escaneo_de_la_noche_cae_en_su_dia_local_y_no_en_el_siguiente(self):
        self.assertEqual(actividad.clave_de(datetime.fromtimestamp(ts(2026, 10, 25, 23, 50)), "dia"), "2026-10-25")

    def test_etiquetas_ya_formateadas(self):
        self.assertEqual(actividad.etiqueta_de("2026-10-05", "dia"), "05 oct")
        self.assertEqual(actividad.etiqueta_de("2026-10-19", "semana"), "sem. 19 oct")
        self.assertEqual(actividad.etiqueta_de("2026-02", "mes"), "feb 2026")
        self.assertEqual(actividad.etiqueta_de("2026", "anio"), "2026")


class PeriodosTests(unittest.TestCase):
    HOY = date(2026, 10, 25)

    def test_dias(self):
        p = actividad.periodos("dia", 3, self.HOY)
        self.assertEqual(p, ["2026-10-23", "2026-10-24", "2026-10-25"])

    def test_semanas_terminan_en_la_semana_en_curso(self):
        self.assertEqual(actividad.periodos("semana", 3, self.HOY), ["2026-10-05", "2026-10-12", "2026-10-19"])

    def test_meses_cruzando_el_cambio_de_anio(self):
        self.assertEqual(actividad.periodos("mes", 4, date(2026, 2, 10)), ["2025-11", "2025-12", "2026-01", "2026-02"])
        self.assertEqual(len(actividad.periodos("mes", 12, self.HOY)), 12)

    def test_anios(self):
        self.assertEqual(actividad.periodos("anio", 3, self.HOY), ["2024", "2025", "2026"])

    def test_sin_repetidos_y_en_orden(self):
        for g in actividad.GRANULARIDADES:
            p = actividad.periodos(g, 20, self.HOY)
            self.assertEqual(p, sorted(set(p)))


class LeerUsosTests(unittest.TestCase):
    def test_junta_el_uso_de_todos_e_ignora_lo_ilegible_y_lo_ajeno(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = alm.AlmacenLocal(d)
            for carpeta in ("aaa", "bbb", "ccc", "ddd"):
                os.makedirs(os.path.join(d, "users", carpeta))
            raiz.escribir_texto("users/aaa/uso.json", json.dumps([30.0, 10.0, 20.0]))
            raiz.escribir_texto("users/bbb/uso.json", "{roto")
            raiz.escribir_texto("users/ccc/otro.json", "[1, 2]")
            raiz.escribir_texto("users/ddd/uso.json", json.dumps(["no es un numero"]))
            os.makedirs(os.path.join(d, "admin"))
            raiz.escribir_texto("admin/uso.json", "[1]")  # fuera de users/: no cuenta
            self.assertEqual(actividad.leer_usos(raiz), {"aaa": [10.0, 20.0, 30.0]})


CORREOS = {"aaa": "ana@x.cl", "bbb": "beto@x.cl"}
USOS = {
    "aaa": [ts(2026, 10, 25, 10), ts(2026, 10, 24, 9), ts(2026, 10, 20, 8), ts(2026, 9, 30, 8), ts(2025, 5, 1, 8)],
    "bbb": [ts(2026, 10, 25, 22)],
    "zzz": [ts(2026, 10, 25, 1)],  # ya no está en la lista
}
HOY = date(2026, 10, 25)


class ConteoTests(unittest.TestCase):
    def test_serie_por_dia_cuenta_por_usuario_y_respeta_la_ventana(self):
        claves = actividad.periodos("dia", 7, HOY)
        filas = actividad.serie(USOS, CORREOS, "dia", claves)
        hoy = {f["usuario"]: f["escaneos"] for f in filas if f["periodo"] == "2026-10-25"}
        self.assertEqual(hoy["ana@x.cl"], 1)
        self.assertEqual(hoy["beto@x.cl"], 1)
        self.assertEqual(hoy[f"{actividad.SIN_USUARIO} zzz"], 1)
        self.assertNotIn("2026-09-30", {f["periodo"] for f in filas})  # fuera de los 7 días

    def test_serie_por_mes_y_por_anio(self):
        meses = actividad.serie(USOS, CORREOS, "mes", actividad.periodos("mes", 12, HOY))
        ana = {f["periodo"]: f["escaneos"] for f in meses if f["usuario"] == "ana@x.cl"}
        self.assertEqual(ana, {"2026-10": 3, "2026-09": 1})  # mayo de 2025 queda fuera de los 12 meses
        anios = actividad.serie(USOS, CORREOS, "anio", actividad.periodos("anio", 3, HOY))
        ana = {f["periodo"]: f["escaneos"] for f in anios if f["usuario"] == "ana@x.cl"}
        self.assertEqual(ana, {"2026": 4, "2025": 1})

    def test_la_suma_de_la_serie_es_el_total_de_la_ventana(self):
        filas = actividad.serie(USOS, CORREOS, "anio", actividad.periodos("anio", 5, HOY))
        self.assertEqual(sum(f["escaneos"] for f in filas), 7)

    def test_totales(self):
        t = actividad.totales(USOS, HOY)
        self.assertEqual((t["hoy"], t["total"]), (3, 7))
        self.assertEqual(t["semana"], 5)  # del lunes 19 al domingo 25: ana x3 (25, 24 y 20), beto x1 y el que ya no está x1
        self.assertEqual(t["mes"], 5)
        self.assertEqual(t["anio"], 6)
        self.assertAlmostEqual(t["ultimo"], ts(2026, 10, 25, 22))

    def test_totales_sin_datos(self):
        self.assertEqual(actividad.totales({}, HOY), {"hoy": 0, "semana": 0, "mes": 0, "anio": 0, "total": 0, "ultimo": None})

    def test_tabla_por_usuario(self):
        tabla = actividad.tabla_por_usuario(USOS, CORREOS, HOY)
        self.assertEqual([f["usuario"] for f in tabla][0], "ana@x.cl")  # el que más escanea primero
        ana = tabla[0]
        self.assertEqual((ana["total"], ana["este_mes"], ana["vigente"]), (5, 3, True))
        zzz = next(f for f in tabla if f["usuario"].startswith(actividad.SIN_USUARIO))
        self.assertFalse(zzz["vigente"])
        self.assertEqual(len(tabla), 3)


class GraficoTests(unittest.TestCase):
    @staticmethod
    def filas(usuarios):
        return [{"periodo": "2026-10-25", "etiqueta": "25 oct", "usuario": u, "escaneos": i + 1} for i, u in enumerate(usuarios)]

    def test_hasta_siete_usuarios_conservan_su_serie(self):
        datos = actividad_grafico.agrupar_usuarios(self.filas([f"u{i}@x.cl" for i in range(7)]))
        self.assertEqual({d["usuario"] for d in datos}, {f"u{i}@x.cl" for i in range(7)})
        self.assertNotIn("Otros", {d["usuario"] for d in datos})

    def test_pasados_siete_el_resto_se_suma_en_otros_sin_perder_escaneos(self):
        original = self.filas([f"u{i}@x.cl" for i in range(9)])
        datos = actividad_grafico.agrupar_usuarios(original)
        self.assertEqual(len({d["usuario"] for d in datos}), 7)  # 6 propios + "Otros"
        self.assertEqual(sum(d["escaneos"] for d in datos), sum(d["escaneos"] for d in original))
        self.assertEqual([d["escaneos"] for d in datos if d["usuario"] == "Otros"], [7 + 8 + 9])

    def test_cada_usuario_conserva_su_color_y_otros_va_en_gris(self):
        datos = actividad_grafico.agrupar_usuarios(self.filas([f"u{i}@x.cl" for i in range(9)]))
        for oscuro in (False, True):
            colores = actividad_grafico.colores_por_usuario(datos, oscuro)
            self.assertEqual(colores["Otros"], "#898781")
            self.assertEqual(len(set(colores.values())), len(colores))  # ninguno repetido
        solo_dos = actividad_grafico.agrupar_usuarios(self.filas(["u0@x.cl", "u1@x.cl"]))
        self.assertEqual(actividad_grafico.colores_por_usuario(solo_dos, False)["u0@x.cl"],
                         actividad_grafico.colores_por_usuario(datos, False)["u0@x.cl"])  # el mismo color con más o menos gente

    def test_construye_un_grafico(self):
        datos = actividad_grafico.agrupar_usuarios(self.filas(["u0@x.cl", "u1@x.cl"]))
        grafico = actividad_grafico.construir(datos, ["25 oct"], True, "día")
        self.assertIn("escaneos", grafico.to_json())


if __name__ == "__main__":
    unittest.main()
