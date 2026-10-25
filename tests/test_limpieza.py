"""Tests de comun/limpieza.py: qué se puede borrar del bucket y, sobre todo, qué NUNCA.
    python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from comun import almacen as alm
from comun import limpieza, usuarios

AHORA = 10_000_000.0  # lo bastante grande para restar 60 dias sin salir de la epoca
DIA = 24 * 3600

ANA = alm.id_usuario("ana@x.cl")
JEFE = alm.id_usuario("jefe@x.cl")
EXTRANO = "0123456789abcdef"  # un id que no corresponde a nadie de la lista


def lista():
    d = usuarios.agregar(usuarios.vacio(), "jefe@x.cl", "admin")
    return usuarios.agregar(d, "ana@x.cl", "usuario")


def obj(nombre, bytes_=100, hace=0.0):
    return {"nombre": nombre, "bytes": bytes_, "actualizado": AHORA - hace}


class EsBorrableTests(unittest.TestCase):
    VIGENTES = limpieza.ids_vigentes(lista())

    def test_la_lista_de_usuarios_nunca_se_borra(self):
        self.assertFalse(limpieza.es_borrable("admin/usuarios.json", self.VIGENTES))
        self.assertFalse(limpieza.es_borrable("admin/usuarios.json", {}))  # ni siquiera sin vigentes
        self.assertFalse(limpieza.es_borrable("admin/usuarios.json", {"cualquiera": "x"}))

    def test_los_archivos_de_usuarios_vigentes_nunca_se_borran(self):
        for archivo in ("ofertas_empleos.csv", "keywords.txt", "preferencias.json", "uso.json", "scan_status.json", "keywords_gui_actual.txt"):
            self.assertFalse(limpieza.es_borrable(f"users/{ANA}/{archivo}", self.VIGENTES), archivo)
            self.assertFalse(limpieza.es_borrable(f"users/{JEFE}/{archivo}", self.VIGENTES), archivo)

    def test_las_carpetas_que_no_estan_en_la_lista_si_se_pueden_borrar(self):
        for archivo in ("ofertas_empleos.csv", "keywords.txt", "scan_status.json"):
            self.assertTrue(limpieza.es_borrable(f"users/{EXTRANO}/{archivo}", self.VIGENTES))
        self.assertTrue(limpieza.es_borrable("users/local/ofertas_empleos.csv", self.VIGENTES))  # las pruebas viejas
        self.assertTrue(limpieza.es_borrable("users/prueba/scan_status.json", self.VIGENTES))

    def test_los_temporales_se_borran_incluso_en_carpetas_vigentes(self):
        self.assertTrue(limpieza.es_borrable(f"users/{ANA}/lanzando.lock", self.VIGENTES))
        self.assertTrue(limpieza.es_borrable(f"users/{ANA}/detener.flag", self.VIGENTES))

    def test_un_temporal_escondido_mas_adentro_no_cuenta(self):
        self.assertFalse(limpieza.es_borrable(f"users/{ANA}/sub/lanzando.lock", self.VIGENTES))

    def test_lo_obsoleto_conocido_se_borra_y_el_resto_de_admin_no(self):
        self.assertTrue(limpieza.es_borrable("admin/ajustes.json", self.VIGENTES))
        self.assertFalse(limpieza.es_borrable("admin/otra_cosa.json", self.VIGENTES))
        self.assertFalse(limpieza.es_borrable("admin/", self.VIGENTES))

    def test_nombres_que_intentan_colarse_se_rechazan(self):
        peligrosos = [
            "users/../admin/usuarios.json", f"users/{EXTRANO}/../../admin/usuarios.json", "users//x/y", f"users/{EXTRANO}//y",
            "users/", "users", f"users/{EXTRANO}", f"users/{EXTRANO}/", "../users/x/y", "", "/", "users\\x\\y.csv",
            "otra/carpeta/archivo", "ofertas_empleos.csv", f"users/{EXTRANO}/a/", "USERS/x/y",
        ]
        for nombre in peligrosos:
            self.assertFalse(limpieza.es_borrable(nombre, self.VIGENTES), repr(nombre))

    def test_id_de_carpeta(self):
        self.assertEqual(limpieza.id_de_carpeta("users/abc/x.json"), "abc")
        self.assertEqual(limpieza.id_de_carpeta("users/abc/sub/x.json"), "abc")
        for malo in ("admin/usuarios.json", "users/abc", "users/abc/", "users//x", "users/../x/y", "users/a\\b/c"):
            self.assertIsNone(limpieza.id_de_carpeta(malo), malo)


class AnalizarTests(unittest.TestCase):
    def objetos(self):
        return [
            obj("admin/usuarios.json", 300),
            obj(f"users/{ANA}/ofertas_empleos.csv", 2000), obj(f"users/{ANA}/uso.json", 20),
            obj(f"users/{JEFE}/ofertas_empleos.csv", 500),
            obj(f"users/{EXTRANO}/ofertas_empleos.csv", 700, hace=30 * DIA), obj(f"users/{EXTRANO}/keywords.txt", 30, hace=40 * DIA),
            obj("users/local/scan_status.json", 50),
            obj(f"users/{ANA}/lanzando.lock", 10, hace=2 * DIA),  # vencido
            obj(f"users/{JEFE}/detener.flag", 0, hace=60),  # reciente: no es basura todavía
            obj("admin/ajustes.json", 40),
        ]

    def test_resumen_y_uso_del_free_tier(self):
        a = limpieza.analizar(self.objetos(), lista(), AHORA)
        self.assertEqual(a["objetos"], 10)
        self.assertEqual(a["bytes"], 300 + 2000 + 20 + 500 + 700 + 30 + 50 + 10 + 0 + 40)
        self.assertAlmostEqual(a["porcentaje_gratis"], a["bytes"] / (5 * 1024 ** 3))

    def test_detecta_las_carpetas_huerfanas_y_no_toca_las_vigentes(self):
        a = limpieza.analizar(self.objetos(), lista(), AHORA)
        self.assertEqual({c["id"] for c in a["huerfanas"]}, {EXTRANO, "local"})
        vigentes = {c["id"]: c for c in a["carpetas"] if c["vigente"]}
        self.assertEqual(set(vigentes), {ANA, JEFE})
        self.assertEqual(vigentes[ANA]["correo"], "ana@x.cl")
        huerfana = next(c for c in a["huerfanas"] if c["id"] == EXTRANO)
        self.assertEqual((huerfana["objetos"], huerfana["bytes"]), (2, 730))
        self.assertAlmostEqual(huerfana["ultima_actividad"], AHORA - 30 * DIA)  # la más reciente de sus archivos

    def test_temporales_solo_los_vencidos(self):
        a = limpieza.analizar(self.objetos(), lista(), AHORA)
        self.assertEqual(a["temporales_vencidos"], [f"users/{ANA}/lanzando.lock"])

    def test_obsoletos(self):
        self.assertEqual(limpieza.analizar(self.objetos(), lista(), AHORA)["obsoletos"], ["admin/ajustes.json"])

    def test_todo_lo_que_propone_el_analisis_pasa_la_guarda(self):
        a = limpieza.analizar(self.objetos(), lista(), AHORA)
        vigentes = limpieza.ids_vigentes(lista())
        propuestos = a["temporales_vencidos"] + a["obsoletos"] + [n for c in a["huerfanas"] for n in c["nombres"]]
        self.assertTrue(propuestos)
        self.assertTrue(all(limpieza.es_borrable(n, vigentes) for n in propuestos))

    def test_bucket_vacio_o_solo_admin(self):
        self.assertEqual(limpieza.analizar([], lista(), AHORA)["huerfanas"], [])
        solo_admin = limpieza.analizar([obj("admin/usuarios.json")], lista(), AHORA)
        self.assertEqual((solo_admin["carpetas"], solo_admin["huerfanas"]), ([], []))


class EjecutarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = alm.AlmacenLocal(self.tmp.name)
        for nombre in ("admin/usuarios.json", f"users/{ANA}/ofertas_empleos.csv", f"users/{EXTRANO}/ofertas_empleos.csv",
                       f"users/{EXTRANO}/keywords.txt", "users/local/scan_status.json", f"users/{ANA}/lanzando.lock"):
            os.makedirs(os.path.dirname(os.path.join(self.tmp.name, nombre)), exist_ok=True)
            self.a.escribir(nombre, b"x")
        self.vigentes = limpieza.ids_vigentes(lista())

    def tearDown(self):
        self.tmp.cleanup()

    def test_borra_lo_pedido_y_nada_mas(self):
        n = limpieza.ejecutar(self.a, [f"users/{EXTRANO}/ofertas_empleos.csv", f"users/{EXTRANO}/keywords.txt", "users/local/scan_status.json",
                                       f"users/{ANA}/lanzando.lock"], self.vigentes)
        self.assertEqual(n, 4)
        self.assertEqual(self.a.listar(), ["admin/usuarios.json", f"users/{ANA}/ofertas_empleos.csv"])

    def test_si_un_solo_nombre_no_es_borrable_no_se_borra_ninguno(self):
        pedido = [f"users/{EXTRANO}/ofertas_empleos.csv", f"users/{ANA}/ofertas_empleos.csv"]  # el segundo es de una vigente
        antes = self.a.listar()
        with self.assertRaises(limpieza.ErrorLimpieza) as ctx:
            limpieza.ejecutar(self.a, pedido, self.vigentes)
        self.assertEqual(self.a.listar(), antes)  # ni siquiera el primero, que sí era borrable
        self.assertNotIn(ANA, str(ctx.exception))  # el mensaje no revela rutas

    def test_la_lista_de_usuarios_no_se_puede_borrar_ni_pidiendolo(self):
        with self.assertRaises(limpieza.ErrorLimpieza):
            limpieza.ejecutar(self.a, ["admin/usuarios.json"], self.vigentes)
        self.assertTrue(self.a.existe("admin/usuarios.json"))

    def test_repetidos_y_lista_vacia(self):
        nombre = f"users/{EXTRANO}/keywords.txt"
        self.assertEqual(limpieza.ejecutar(self.a, [nombre, nombre], self.vigentes), 1)
        self.assertEqual(limpieza.ejecutar(self.a, [], self.vigentes), 0)

    def test_listar_detalle_local_trae_tamano_y_fecha(self):
        detalle = {d["nombre"]: d for d in self.a.listar_detalle("users/")}
        self.assertEqual(detalle[f"users/{EXTRANO}/keywords.txt"]["bytes"], 1)
        self.assertGreater(detalle[f"users/{EXTRANO}/keywords.txt"]["actualizado"], 0)
        self.assertEqual(sorted(d["nombre"] for d in self.a.listar_detalle("admin/")), ["admin/usuarios.json"])


class EstadosColgadosTests(unittest.TestCase):
    def test_solo_los_corriendo_sin_senales_hace_mas_de_un_dia(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = alm.AlmacenLocal(tmp)
            estados = {ANA: {"corriendo": True, "actualizado_ts": AHORA - 3 * DIA},   # murió hace días
                       JEFE: {"corriendo": True, "actualizado_ts": AHORA - 60}}        # corre de verdad
            objetos = []
            for uid, estado in estados.items():
                os.makedirs(os.path.join(tmp, "users", uid))
                a.escribir_texto(f"users/{uid}/scan_status.json", json.dumps(estado))
                objetos.append(obj(f"users/{uid}/scan_status.json"))
            analisis = limpieza.analizar(objetos, lista(), AHORA)
            self.assertEqual(limpieza.estados_colgados(a, analisis, AHORA), [f"users/{ANA}/scan_status.json"])

    def test_un_estado_ilegible_se_ignora(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = alm.AlmacenLocal(tmp)
            os.makedirs(os.path.join(tmp, "users", ANA))
            a.escribir_texto(f"users/{ANA}/scan_status.json", "{roto")
            analisis = limpieza.analizar([obj(f"users/{ANA}/scan_status.json")], lista(), AHORA)
            self.assertEqual(limpieza.estados_colgados(a, analisis, AHORA), [])


class _BucketFalso:
    def __init__(self, reglas=None):
        self.lifecycle_rules = list(reglas or [])
        self.parches = 0

    def add_lifecycle_delete_rule(self, **kw):
        self.lifecycle_rules.append({"action": {"type": "Delete"},
                                     "condition": {"age": kw["age"], "matchesSuffix": kw["matches_suffix"]}})

    def patch(self):
        self.parches += 1


class _ClienteFalso:
    def __init__(self, bucket):
        self.bucket = bucket

    def get_bucket(self, nombre):
        return self.bucket


class ReglaAutomaticaTests(unittest.TestCase):
    CFG = {"project": "mi-proyecto-123", "bucket": "mi-bucket"}

    def _importar(self):
        from administracion import admin_nube

        return admin_nube

    def test_activa_la_regla_una_sola_vez_y_solo_para_lo_efimero(self):
        nube = self._importar()
        bucket = _BucketFalso()
        cliente = _ClienteFalso(bucket)
        self.assertFalse(nube.regla_de_temporales_activa(self.CFG, cliente=cliente))
        self.assertTrue(nube.activar_regla_de_temporales(self.CFG, cliente=cliente))
        regla = bucket.lifecycle_rules[0]
        self.assertEqual(regla["condition"]["age"], 1)
        self.assertEqual(sorted(regla["condition"]["matchesSuffix"]), ["/detener.flag", "/lanzando.lock"])
        # solo esos dos nombres: ninguna regla automática puede alcanzar archivos de datos de los usuarios
        for dato in ("/ofertas_empleos.csv", "/keywords.txt", "/preferencias.json", "/uso.json", "/scan_status.json", "/usuarios.json"):
            self.assertNotIn(dato, regla["condition"]["matchesSuffix"])
        self.assertTrue(nube.regla_de_temporales_activa(self.CFG, cliente=cliente))
        self.assertFalse(nube.activar_regla_de_temporales(self.CFG, cliente=cliente))  # idempotente
        self.assertEqual((len(bucket.lifecycle_rules), bucket.parches), (1, 1))

    def test_no_pisa_las_reglas_que_ya_existian(self):
        nube = self._importar()
        previa = {"action": {"type": "Delete"}, "condition": {"age": 365, "matchesSuffix": [".tmp"]}}
        bucket = _BucketFalso([previa])
        nube.activar_regla_de_temporales(self.CFG, cliente=_ClienteFalso(bucket))
        self.assertIn(previa, bucket.lifecycle_rules)
        self.assertEqual(len(bucket.lifecycle_rules), 2)

    def test_una_regla_parecida_pero_distinta_no_cuenta_como_activa(self):
        nube = self._importar()
        otra_edad = {"action": {"type": "Delete"}, "condition": {"age": 30, "matchesSuffix": ["/lanzando.lock", "/detener.flag"]}}
        otros_sufijos = {"action": {"type": "Delete"}, "condition": {"age": 1, "matchesSuffix": ["/keywords.txt"]}}
        otra_accion = {"action": {"type": "SetStorageClass"}, "condition": {"age": 1, "matchesSuffix": ["/lanzando.lock", "/detener.flag"]}}
        for regla in (otra_edad, otros_sufijos, otra_accion):
            self.assertFalse(nube.regla_de_temporales_activa(self.CFG, cliente=_ClienteFalso(_BucketFalso([regla]))))


if __name__ == "__main__":
    unittest.main()
