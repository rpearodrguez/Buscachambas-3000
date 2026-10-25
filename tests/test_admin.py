"""Tests de usuarios.py y admin_nube.py (validaciones y configuración local; no tocan la nube).
    python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from administracion import admin_nube
from comun import almacen as alm
from comun import usuarios


class UsuariosTests(unittest.TestCase):
    def test_correo_se_normaliza_y_valida(self):
        self.assertEqual(usuarios.validar_correo("  Ana@Correo.COM "), "ana@correo.com")
        for malo in ("", "sin-arroba", "a@b", "a b@c.cl", "a@@c.cl", "x" * 300 + "@c.cl", None):
            with self.assertRaises(usuarios.ErrorUsuarios):
                usuarios.validar_correo(malo)

    def test_agregar_no_muta_el_original_y_actualiza(self):
        base = usuarios.vacio()
        con_ana = usuarios.agregar(base, "Ana@x.cl", "admin", 3)
        self.assertEqual(base, usuarios.vacio())
        self.assertEqual(con_ana["usuarios"]["ana@x.cl"], {"rol": "admin", "cuota_semanal": 3})
        cambiado = usuarios.agregar(con_ana, "ana@x.cl", "usuario", 1)
        self.assertEqual(cambiado["usuarios"]["ana@x.cl"]["rol"], "usuario")

    def test_cuota_y_rol_invalidos(self):
        for cuota in (-1, 51, "abc", None):
            with self.assertRaises(usuarios.ErrorUsuarios):
                usuarios.agregar(usuarios.vacio(), "a@x.cl", "usuario", cuota)
        with self.assertRaises(usuarios.ErrorUsuarios):
            usuarios.agregar(usuarios.vacio(), "a@x.cl", "root", 1)

    def test_cuota_por_defecto_es_una_por_semana(self):
        d = usuarios.agregar(usuarios.vacio(), "a@x.cl")
        self.assertEqual(d["usuarios"]["a@x.cl"]["cuota_semanal"], 1)

    def test_quitar_no_deja_sin_administradores(self):
        d = usuarios.agregar(usuarios.vacio(), "jefe@x.cl", "admin")
        d = usuarios.agregar(d, "ana@x.cl", "usuario")
        with self.assertRaises(usuarios.ErrorUsuarios):
            usuarios.quitar(d, "jefe@x.cl")
        sin_ana = usuarios.quitar(d, "ANA@x.cl")
        self.assertNotIn("ana@x.cl", sin_ana["usuarios"])
        self.assertIn("ana@x.cl", d["usuarios"])  # el original intacto
        with self.assertRaises(usuarios.ErrorUsuarios):
            usuarios.quitar(d, "nadie@x.cl")

    def test_permisos(self):
        d = usuarios.agregar(usuarios.agregar(usuarios.vacio(), "jefe@x.cl", "admin"), "ana@x.cl")
        self.assertTrue(usuarios.es_permitido(d, "ANA@x.cl"))
        self.assertFalse(usuarios.es_permitido(d, "otro@x.cl"))
        self.assertTrue(usuarios.es_admin(d, "jefe@x.cl"))
        self.assertFalse(usuarios.es_admin(d, "ana@x.cl"))
        self.assertFalse(usuarios.es_admin(d, "otro@x.cl"))

    def test_guardar_y_cargar_en_almacen(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = alm.AlmacenLocal(tmp)
            self.assertEqual(usuarios.cargar(a), usuarios.vacio())
            d = usuarios.agregar(usuarios.vacio(), "Ana@x.cl", "admin", 2)
            usuarios.guardar(a, d)
            self.assertEqual(usuarios.cargar(a), d)

    def test_archivo_danado_corta_con_error_claro(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = alm.AlmacenLocal(tmp)
            for basura in ("{no es json", '{"otra": 1}', '{"usuarios": []}'):
                a.escribir_texto(usuarios.ARCHIVO, basura)
                with self.assertRaises(usuarios.ErrorUsuarios):
                    usuarios.cargar(a)


class ConfigLocalTests(unittest.TestCase):
    OK = {"project": "mi-proyecto-123", "region": "us-east1", "job": "mi-job", "bucket": "mi-bucket", "secret": "mi-secreto"}

    def test_ida_y_vuelta_y_nombres(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "c.json")
            admin_nube.guardar_config_local(self.OK, ruta)
            cfg = admin_nube.cargar_config_local(ruta)
            self.assertEqual(cfg, self.OK)
            self.assertEqual(admin_nube.nombre_job(cfg), "projects/mi-proyecto-123/locations/us-east1/jobs/mi-job")
            self.assertEqual(admin_nube.destino_admin(cfg), "gs://mi-bucket/admin")

    def test_sin_archivo_o_dañado_devuelve_none(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "c.json")
            self.assertIsNone(admin_nube.cargar_config_local(ruta))
            with open(ruta, "w") as f:
                f.write("{roto")
            self.assertIsNone(admin_nube.cargar_config_local(ruta))

    def test_campos_invalidos_se_rechazan(self):
        for campo, malo in (("project", "MAYUS"), ("project", "a;b"), ("region", "x y"), ("job", "../x"),
                            ("bucket", "gs://b"), ("secret", "con espacio"), ("job", "")):
            cfg = dict(self.OK, **{campo: malo})
            with self.assertRaises(admin_nube.ErrorAdmin):
                admin_nube.validar_config(cfg)


class SmtpTests(unittest.TestCase):
    def test_validos(self):
        self.assertEqual(admin_nube.validar_smtp(" SMTP.Proton.ch ", "587", " u@x.cl "), ("smtp.proton.ch", 587, "u@x.cl"))

    def test_invalidos(self):
        for host, puerto, usuario in (("", 587, "u"), ("https://h.cl", 587, "u"), ("h.cl:587", 587, "u"),
                                       ("h.cl", 0, "u"), ("h.cl", 70000, "u"), ("h.cl", "abc", "u"),
                                       ("h.cl", 587, ""), ("h.cl", 587, "con espacio")):
            with self.assertRaises(admin_nube.ErrorAdmin):
                admin_nube.validar_smtp(host, puerto, usuario)

    def test_token_vacio_se_rechaza_antes_de_importar_google(self):
        with self.assertRaises(admin_nube.ErrorAdmin):
            admin_nube.reemplazar_token({"project": "p", "secret": "s"}, "   ")


if __name__ == "__main__":
    unittest.main()
