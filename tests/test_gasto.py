"""Tests de gasto.py (lógica pura de la pestaña Gastos) y de la validación de la tabla de facturación.
    python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from administracion import admin_nube, gasto

FILAS = [
    {"dia": "2026-10-01", "proyecto": "p1", "servicio": "Cloud Run", "moneda": "USD", "bruto": 0.10, "creditos": -0.10},
    {"dia": "2026-10-01", "proyecto": "p1", "servicio": "Cloud Storage", "moneda": "USD", "bruto": 0.02, "creditos": -0.02},
    {"dia": "2026-10-01", "proyecto": "p2", "servicio": "Compute Engine", "moneda": "USD", "bruto": 0.30, "creditos": -0.28},
    {"dia": "2026-10-02", "proyecto": "p1", "servicio": "Cloud Run", "moneda": "USD", "bruto": 0.05, "creditos": -0.04},
    {"dia": "2026-10-02", "proyecto": "p1", "servicio": "Cloud Logging", "moneda": "USD", "bruto": 0.01, "creditos": 0.0},
    {"dia": "2026-10-02", "proyecto": "p1", "servicio": "Networking", "moneda": "USD", "bruto": 0.02, "creditos": 0.0},
]


class MontosTests(unittest.TestCase):
    def test_bruto_y_neto(self):
        f = FILAS[2]
        self.assertAlmostEqual(gasto.monto(f, "bruto"), 0.30)
        self.assertAlmostEqual(gasto.monto(f, "neto"), 0.02)  # créditos en negativo
        with self.assertRaises(ValueError):
            gasto.monto(f, "otra")

    def test_totales(self):
        t = gasto.totales(FILAS)
        self.assertAlmostEqual(t["bruto"], 0.50)
        self.assertAlmostEqual(t["creditos"], -0.44)
        self.assertAlmostEqual(t["neto"], 0.06)

    def test_totales_vacio(self):
        self.assertEqual(gasto.totales([]), {"bruto": 0, "creditos": 0, "neto": 0})

    def test_una_sola_moneda(self):
        mezcla = FILAS + [dict(FILAS[0], moneda="CLP", bruto=1000.0)]
        self.assertEqual(gasto.moneda_principal(mezcla), "USD")
        self.assertEqual(len(gasto.solo_moneda(mezcla, "USD")), len(FILAS))

    def test_formatear_no_redondea_centavos_a_cero(self):
        self.assertEqual(gasto.formatear(0.0123, "USD"), "0.0123 USD")
        self.assertEqual(gasto.formatear(-0.5, "USD"), "-0.5000 USD")


class SerieTests(unittest.TestCase):
    def test_desconocidos_se_pliegan_en_otros(self):
        datos = gasto.serie_diaria(FILAS, "bruto")
        dia2 = {d["servicio"]: d["monto"] for d in datos if d["dia"] == "2026-10-02"}
        self.assertEqual(set(dia2), {"Cloud Run", "Networking", "Otros"})  # Networking tiene color propio
        self.assertAlmostEqual(dia2["Otros"], 0.01)  # solo Cloud Logging
        self.assertAlmostEqual(dia2["Networking"], 0.02)
        self.assertAlmostEqual(dia2["Cloud Run"], 0.05)

    def test_neto_usa_los_creditos(self):
        datos = gasto.serie_diaria(FILAS, "neto")
        dia1 = {d["servicio"]: d["monto"] for d in datos if d["dia"] == "2026-10-01"}
        self.assertAlmostEqual(dia1["Cloud Run"], 0.0)
        self.assertAlmostEqual(dia1["Compute Engine"], 0.02)

    def test_etiqueta_de_dia_no_depende_de_zonas_horarias(self):
        self.assertEqual(gasto.etiqueta_de_dia("2026-10-01"), "01 oct")
        self.assertEqual(gasto.etiqueta_de_dia("2026-12-31T00:00:00"), "31 dic")
        self.assertEqual(gasto.etiqueta_de_dia("2026-01-09"), "09 ene")

    def test_apilado_y_etiquetas_en_orden(self):
        datos = gasto.serie_diaria(FILAS, "bruto")
        orden = {d["servicio"]: d["orden"] for d in datos}
        self.assertEqual(orden["Cloud Run"], 0)
        self.assertGreater(orden["Otros"], orden["Compute Engine"])  # "Otros" va arriba del todo
        self.assertEqual(gasto.etiquetas_en_orden(datos), ["01 oct", "02 oct"])

    def test_la_vm_y_su_red_tienen_color_propio(self):
        for servicio in ("Compute Engine", "Networking", "VM Manager"):
            self.assertIn(servicio, gasto.SERVICIOS_CONOCIDOS)

    def test_los_ajustes_de_factura_no_se_grafican_pero_si_cuentan_en_totales_y_tabla(self):
        con_ajuste = FILAS + [{"dia": "2026-10-01", "proyecto": "p1", "servicio": "Invoice", "moneda": "USD", "bruto": -0.0025, "creditos": 0.0}]
        datos = gasto.serie_diaria(con_ajuste, "bruto")
        self.assertTrue(all(d["monto"] >= 0 for d in datos))  # ninguna barra bajo cero
        self.assertNotIn("Invoice", {d["servicio"] for d in datos})
        self.assertNotIn("Otros", {d["servicio"] for d in datos if d["dia"] == "2026-10-01"})  # el ajuste no se pliega en "Otros"
        self.assertAlmostEqual(gasto.totales(con_ajuste)["bruto"], 0.50 - 0.0025)
        self.assertIn("Invoice", [r["servicio"] for r in gasto.tabla_por_servicio(con_ajuste)])

    def test_dias_del_periodo_incluye_los_dias_sin_datos(self):
        from datetime import date

        dias = gasto.dias_del_periodo(5, date(2026, 10, 25))
        self.assertEqual(dias, ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"])
        self.assertEqual(len(gasto.dias_del_periodo(90, date(2026, 10, 25))), 90)
        self.assertEqual(gasto.dias_del_periodo(1, date(2026, 1, 1)), ["2026-01-01"])

    def test_dias_ordenados(self):
        dias = [d["dia"] for d in gasto.serie_diaria(FILAS, "bruto")]
        self.assertEqual(dias, sorted(dias))

    def test_series_presentes_en_orden_fijo_con_otros_al_final(self):
        datos = gasto.serie_diaria(FILAS, "bruto")
        self.assertEqual(gasto.series_presentes(datos), ["Cloud Run", "Compute Engine", "Networking", "Cloud Storage", "Otros"])

    def test_el_color_sigue_a_la_entidad_no_al_ranking(self):
        solo_run = gasto.series_presentes(gasto.serie_diaria([FILAS[0]], "bruto"))
        todos = gasto.series_presentes(gasto.serie_diaria(FILAS, "bruto"))
        for oscuro in (False, True):
            # Cloud Run conserva su color aunque aparezcan o desaparezcan otros servicios
            self.assertEqual(gasto.color_de("Cloud Run", oscuro), gasto.color_de(todos[0], oscuro))
            self.assertEqual(solo_run, ["Cloud Run"])
        self.assertEqual(gasto.color_de("Otros", False), gasto.GRIS_OTROS)
        self.assertEqual(gasto.color_de("Otros", True), gasto.GRIS_OTROS)

    def test_colores_distintos_en_cada_modo_y_sin_repetirse(self):
        for paleta in (gasto.PALETA_CLARA, gasto.PALETA_OSCURA):
            self.assertEqual(len(set(paleta)), len(paleta))
            self.assertEqual(len(paleta), len(gasto.SERVICIOS_CONOCIDOS))
            self.assertNotIn(gasto.GRIS_OTROS, paleta)


class TablaYFiltrosTests(unittest.TestCase):
    def test_tabla_no_pliega_y_ordena_por_bruto(self):
        tabla = gasto.tabla_por_servicio(FILAS)
        self.assertEqual([r["servicio"] for r in tabla][:2], ["Compute Engine", "Cloud Run"])
        self.assertIn("Networking", [r["servicio"] for r in tabla])
        run = next(r for r in tabla if r["servicio"] == "Cloud Run")
        self.assertAlmostEqual(run["bruto"], 0.15)
        self.assertAlmostEqual(run["neto"], 0.01)

    def test_proyectos_y_filtro(self):
        self.assertEqual(gasto.proyectos(FILAS), ["p1", "p2"])
        self.assertEqual({f["proyecto"] for f in gasto.filtrar_proyecto(FILAS, "p2")}, {"p2"})
        self.assertEqual(gasto.filtrar_proyecto(FILAS, None), FILAS)


class ValidacionTablaTests(unittest.TestCase):
    BASE = {"project": "mi-proyecto-123", "region": "us-east1", "job": "mi-job", "bucket": "mi-bucket", "secret": "mi-secreto"}

    def test_tabla_valida_y_opcional(self):
        sin = admin_nube.validar_config(self.BASE)
        self.assertNotIn("billing_tabla", sin)
        con = admin_nube.validar_config(dict(self.BASE, billing_tabla=" mi-proyecto.facturacion.gcp_billing_export_v1_AAAA_BBBB_CCCC "))
        self.assertEqual(con["billing_tabla"], "mi-proyecto.facturacion.gcp_billing_export_v1_AAAA_BBBB_CCCC")
        vacia = admin_nube.validar_config(dict(self.BASE, billing_tabla="  "))
        self.assertNotIn("billing_tabla", vacia)

    def test_tablas_peligrosas_se_rechazan(self):
        for mala in ("solo_nombre", "a.b", "p.d.t`; DROP TABLE x; --", "p.d.t with space", "p.d.t) UNION SELECT 1", "a.b.c.d"):
            with self.assertRaises(admin_nube.ErrorAdmin):
                admin_nube.validar_config(dict(self.BASE, billing_tabla=mala))

    def test_consultar_sin_tabla_o_con_periodo_absurdo(self):
        with self.assertRaises(admin_nube.ErrorAdmin):
            admin_nube.consultar_gasto({"project": "p"}, 30)
        with self.assertRaises(admin_nube.ErrorAdmin):
            admin_nube.consultar_gasto({"project": "p", "billing_tabla": "p.d.t_t"}, 0)
        with self.assertRaises(admin_nube.ErrorAdmin):
            admin_nube.consultar_gasto({"project": "p", "billing_tabla": "p.d.t_t"}, 5000)
        with self.assertRaises(admin_nube.ErrorAdmin):
            admin_nube.consultar_gasto({"project": "p", "billing_tabla": "p.d.t`;--"}, 30)


    def test_ultimo_dia_sin_tabla_o_con_tabla_peligrosa_se_rechaza(self):
        with self.assertRaises(admin_nube.ErrorAdmin):
            admin_nube.ultimo_dia_con_datos({"project": "p"})
        with self.assertRaises(admin_nube.ErrorAdmin):
            admin_nube.ultimo_dia_con_datos({"project": "p", "billing_tabla": "p.d.t`;--"})


if __name__ == "__main__":
    unittest.main()
