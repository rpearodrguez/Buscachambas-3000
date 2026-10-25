"""Prueba de punta a punta de la cuota semanal en la GUI de la nube (sin tocar Google Cloud):
sesión simulada, almacenamiento en una carpeta temporal y el lanzamiento del job reemplazado por un registro.
    python -m unittest tests.test_cuota_gui
"""
import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
from streamlit.testing.v1 import AppTest

from comun import acceso, lanzador, usuarios
from comun import almacen as alm

GUI = os.path.join(os.path.dirname(__file__), "..", "app", "gui.py")


class CuotaEnLaGuiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = self.dir.name
        datos = usuarios.vacio()
        datos = usuarios.agregar(datos, "ana@x.cl", "usuario", 1)
        datos = usuarios.agregar(datos, "jefe@x.cl", "admin", 1)
        os.makedirs(os.path.join(base, "admin"))
        usuarios.guardar(alm.AlmacenLocal(os.path.join(base, "admin")), datos)
        self.base = base
        self.lanzamientos = []
        self._job, self._login, self._user = lanzador.lanzar_job, getattr(st, "login", None), st.user

        def job_falso(nombre, args, credenciales=None):
            # no corre nada: registra el pedido y deja el escaneo como ya terminado (si no, la página espera para siempre)
            self.lanzamientos.append(args)
            destino = args[args.index("--storage") + 1]
            carpeta = os.path.join(self.base, *destino[len("gs://falso"):].strip("/").split("/"))
            alm.AlmacenLocal(carpeta).escribir_texto("scan_status.json", json.dumps({"corriendo": False, "resultado": None}))

        lanzador.lanzar_job = job_falso
        self._env = {k: os.environ.get(k) for k in ("BUSCACHAMBAS_JOB", "BUSCACHAMBAS_STORAGE", "BUSCACHAMBAS_MODO")}
        os.environ["BUSCACHAMBAS_JOB"] = "projects/p/locations/r/jobs/j"
        os.environ["BUSCACHAMBAS_STORAGE"] = "gs://falso"
        # "gs://falso/<ruta>" vive en una carpeta temporal: la GUI cree que habla con un bucket
        self._crear = alm.crear_almacen

        def falso(destino, credenciales=None):
            carpeta = os.path.join(base, *destino[len("gs://falso"):].strip("/").split("/")) if destino.strip("/") != "gs:/falso" else base
            os.makedirs(carpeta, exist_ok=True)
            return alm.AlmacenLocal(carpeta)

        alm.crear_almacen = falso
        st.cache_resource.clear()
        st.cache_data.clear()
        os.environ.pop("BUSCACHAMBAS_MODO", None)

    def tearDown(self):
        lanzador.lanzar_job = self._job
        alm.crear_almacen = self._crear
        st.user = self._user
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        self.dir.cleanup()

    def _abrir(self, correo):
        st.user = SimpleNamespace(is_logged_in=True, get=lambda k, d=None: {"email": correo, "email_verified": True}.get(k, d))
        at = AppTest.from_file(GUI, default_timeout=60)
        at.secrets["auth"] = {"redirect_uri": "http://localhost/oauth2callback", "cookie_secret": "x"}
        return at.run()

    def _terminar_scan_falso(self, correo):
        """El job de mentira no corre nada: deja el estado como si el escaneo hubiera terminado."""
        carpeta = os.path.join(self.base, "users", alm.id_usuario(correo))
        os.makedirs(carpeta, exist_ok=True)
        almacen = alm.AlmacenLocal(carpeta)
        almacen.escribir_texto("scan_status.json", json.dumps({"corriendo": False, "resultado": None}))
        almacen.borrar("lanzando.lock")

    def _buscar(self, at):
        at.text_area(key="keywords_text").set_value("python").run()
        next(b for b in at.button if "Buscar ofertas" in b.label).click()
        return at.run()

    def test_un_usuario_con_cuota_uno_escanea_una_vez_y_la_segunda_se_bloquea(self):
        at = self._abrir("ana@x.cl")
        self.assertFalse(at.exception, [str(e.value) for e in at.exception])
        at = self._buscar(at)
        self.assertEqual(len(self.lanzamientos), 1)
        self.assertIn("ana@x.cl", self.lanzamientos[0])  # --usuario viaja al job
        usos = acceso.cargar_usos(alm.AlmacenLocal(os.path.join(self.base, "users", alm.id_usuario("ana@x.cl"))))
        self.assertEqual(len(usos), 1)

        self._terminar_scan_falso("ana@x.cl")
        at = self._abrir("ana@x.cl")
        at = self._buscar(at)
        self.assertEqual(len(self.lanzamientos), 1, "la segunda búsqueda no debió lanzar otro job")
        self.assertTrue(any("cuota" in e.value for e in at.error), [e.value for e in at.error])
        self.assertTrue(any("Podrás volver a escanear" in e.value for e in at.error))

    def test_el_administrador_no_tiene_tope(self):
        for _ in range(3):
            at = self._abrir("jefe@x.cl")
            at = self._buscar(at)
            self._terminar_scan_falso("jefe@x.cl")
        self.assertEqual(len(self.lanzamientos), 3)

    def test_un_correo_fuera_de_la_lista_no_entra(self):
        at = self._abrir("intruso@x.cl")
        self.assertTrue(any("no tiene acceso" in e.value for e in at.error))
        self.assertFalse(any("Buscar ofertas" in b.label for b in at.button))
        self.assertEqual(self.lanzamientos, [])

    def test_la_cuota_de_uno_no_gasta_la_de_otro(self):
        datos = usuarios.cargar(alm.AlmacenLocal(os.path.join(self.base, "admin")))
        usuarios.guardar(alm.AlmacenLocal(os.path.join(self.base, "admin")), usuarios.agregar(datos, "beto@x.cl", "usuario", 1))
        self._buscar(self._abrir("ana@x.cl"))
        self._terminar_scan_falso("ana@x.cl")
        self._buscar(self._abrir("beto@x.cl"))
        self.assertEqual(len(self.lanzamientos), 2)


if __name__ == "__main__":
    unittest.main()
