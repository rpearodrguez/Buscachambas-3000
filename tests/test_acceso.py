"""Tests de acceso.py: quién entra, cuota semanal, candado y tope de escaneos simultáneos.
    python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import acceso
import almacen as alm
import usuarios

DIA = 24 * 3600
AHORA = 1_000_000.0


def datos_base():
    d = usuarios.agregar(usuarios.vacio(), "jefe@x.cl", "admin")
    return usuarios.agregar(d, "ana@x.cl", "usuario", 1)


class DecidirAccesoTests(unittest.TestCase):
    def test_ok_para_usuario_en_la_lista(self):
        motivo, info = acceso.decidir_acceso("Ana@X.cl", True, datos_base())
        self.assertEqual(motivo, "ok")
        self.assertEqual(info["cuota_semanal"], 1)

    def test_falla_cerrado(self):
        d = datos_base()
        self.assertEqual(acceso.decidir_acceso(None, True, d)[0], "sin_correo")
        self.assertEqual(acceso.decidir_acceso("", True, d)[0], "sin_correo")
        for verificado in (False, None, "true", 1):
            self.assertEqual(acceso.decidir_acceso("ana@x.cl", verificado, d)[0], "no_verificado")
        self.assertEqual(acceso.decidir_acceso("intruso@x.cl", True, d)[0], "no_autorizado")
        self.assertEqual(acceso.decidir_acceso("ana@x.cl", True, usuarios.vacio())[0], "no_autorizado")


class CuotaTests(unittest.TestCase):
    USUARIO = {"rol": "usuario", "cuota_semanal": 1}

    def test_sin_usos_puede(self):
        self.assertEqual(acceso.evaluar_cuota(self.USUARIO, [], AHORA), (True, None, None))

    def test_con_un_uso_reciente_no_puede_y_dice_cuando(self):
        usos = [AHORA - 2 * DIA]
        puede, motivo, vuelve = acceso.evaluar_cuota(self.USUARIO, usos, AHORA)
        self.assertFalse(puede)
        self.assertIn("cuota", motivo)
        self.assertEqual(vuelve, usos[0] + 7 * DIA)

    def test_uso_viejo_ya_no_cuenta(self):
        self.assertTrue(acceso.evaluar_cuota(self.USUARIO, [AHORA - 8 * DIA], AHORA)[0])

    def test_cuota_mayor_a_uno(self):
        info = {"rol": "usuario", "cuota_semanal": 2}
        self.assertTrue(acceso.evaluar_cuota(info, [AHORA - DIA], AHORA)[0])
        puede, _, vuelve = acceso.evaluar_cuota(info, [AHORA - 3 * DIA, AHORA - DIA], AHORA)
        self.assertFalse(puede)
        self.assertEqual(vuelve, AHORA - 3 * DIA + 7 * DIA)  # cuando vence el más viejo

    def test_cuota_cero_bloquea_siempre(self):
        self.assertFalse(acceso.evaluar_cuota({"rol": "usuario", "cuota_semanal": 0}, [], AHORA)[0])

    def test_admin_sin_tope(self):
        self.assertTrue(acceso.evaluar_cuota({"rol": "admin", "cuota_semanal": 1}, [AHORA - 60] * 10, AHORA)[0])

    def test_registro_ilegible_bloquea(self):
        self.assertFalse(acceso.evaluar_cuota(self.USUARIO, None, AHORA)[0])

    def test_registrar_y_cargar_usos(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            self.assertEqual(acceso.cargar_usos(a), [])
            acceso.registrar_uso(a, 5.0)
            acceso.registrar_uso(a, 3.0)
            self.assertEqual(acceso.cargar_usos(a), [3.0, 5.0])
            a.escribir_texto(acceso.USO_ARCHIVO, "{roto")
            self.assertIsNone(acceso.cargar_usos(a))

    def test_registro_se_acota(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            for i in range(acceso.MAX_USOS_GUARDADOS + 20):
                acceso.registrar_uso(a, float(i))
            self.assertEqual(len(acceso.cargar_usos(a)), acceso.MAX_USOS_GUARDADOS)


class LockTests(unittest.TestCase):
    def test_un_solo_proceso_toma_el_candado(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            self.assertTrue(acceso.adquirir_lock(a, AHORA))
            self.assertFalse(acceso.adquirir_lock(a, AHORA + 5))
            acceso.liberar_lock(a)
            self.assertTrue(acceso.adquirir_lock(a, AHORA + 6))

    def test_candado_viejo_se_descarta(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            self.assertTrue(acceso.adquirir_lock(a, AHORA))
            self.assertTrue(acceso.adquirir_lock(a, AHORA + acceso.LOCK_TTL + 1))

    def test_candado_ilegible_se_descarta(self):
        with tempfile.TemporaryDirectory() as d:
            a = alm.AlmacenLocal(d)
            a.escribir_texto(acceso.LOCK_ARCHIVO, "basura")
            self.assertTrue(acceso.adquirir_lock(a, AHORA))


class ScansActivosTests(unittest.TestCase):
    def _escribir(self, a, usuario, estado):
        a.escribir_texto(f"users/{usuario}/scan_status.json", json.dumps(estado))

    def test_cuenta_solo_los_que_corren_y_dan_senales(self):
        with tempfile.TemporaryDirectory() as d:
            for u in ("a", "b", "c", "d"):
                os.makedirs(os.path.join(d, "users", u))
            a = alm.AlmacenLocal(d)
            self._escribir(a, "a", {"corriendo": True, "actualizado_ts": AHORA - 5})
            self._escribir(a, "b", {"corriendo": False, "actualizado_ts": AHORA - 5})
            self._escribir(a, "c", {"corriendo": True, "actualizado_ts": AHORA - 10_000})  # colgado
            a.escribir_texto("users/d/scan_status.json", "{roto")
            a.escribir_texto("users/d/otro.json", json.dumps({"corriendo": True, "actualizado_ts": AHORA}))
            self.assertEqual(acceso.contar_scans_activos(a, AHORA), 1)


if __name__ == "__main__":
    unittest.main()
