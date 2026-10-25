"""Tests de comun/ajustes.py (país por defecto elegido por el administrador).
    python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from comun import ajustes
from comun import almacen as alm

PAISES = ["Chile", "Colombia"]


class AjustesTests(unittest.TestCase):
    def test_sin_archivo_son_valores_por_defecto(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            self.assertEqual(ajustes.cargar(a), {})
            self.assertIsNone(ajustes.pais_por_defecto(ajustes.cargar(a), PAISES))

    def test_ida_y_vuelta(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            ajustes.guardar(a, ajustes.con_pais_por_defecto({}, "Colombia", PAISES))
            self.assertEqual(ajustes.pais_por_defecto(ajustes.cargar(a), PAISES), "Colombia")

    def test_con_pais_no_muta_y_valida(self):
        base = {"otro": 1}
        nuevo = ajustes.con_pais_por_defecto(base, "Chile", PAISES)
        self.assertEqual(base, {"otro": 1})
        self.assertEqual(nuevo, {"otro": 1, "pais_por_defecto": "Chile"})
        with self.assertRaises(ValueError):
            ajustes.con_pais_por_defecto({}, "Atlantida", PAISES)

    def test_un_pais_que_ya_no_existe_se_ignora(self):
        self.assertIsNone(ajustes.pais_por_defecto({"pais_por_defecto": "Atlantida"}, PAISES))
        self.assertIsNone(ajustes.pais_por_defecto({"pais_por_defecto": None}, PAISES))

    def test_archivo_danado_no_rompe(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            for basura in ("{roto", "[1, 2]", '"texto"', ""):
                a.escribir_texto(ajustes.ARCHIVO, basura)
                self.assertEqual(ajustes.cargar(a), {})


if __name__ == "__main__":
    unittest.main()
