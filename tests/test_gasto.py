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



def _fila(dia, servicio="Cloud Run", bruto=1.0, creditos=0.0, proyecto="p1"):
    return {"dia": dia, "proyecto": proyecto, "servicio": servicio, "moneda": "USD", "bruto": bruto, "creditos": creditos}


class VistaDiasTests(unittest.TestCase):
    HASTA = __import__("datetime").date(2026, 10, 10)

    def test_recortar_dias_incluye_los_dos_extremos(self):
        filas = [_fila("2026-10-03"), _fila("2026-10-04"), _fila("2026-10-10"), _fila("2026-10-11")]
        dias = [f["dia"] for f in gasto.recortar_dias(filas, 7, self.HASTA)]
        self.assertEqual(dias, ["2026-10-04", "2026-10-10"])  # 7 dias: del 4 al 10; el 3 y el 11 quedan fuera

    def test_comparar_periodos_detecta_crecimiento_y_reduccion(self):
        # actual (4-10 oct): 3.0 ; anterior (27 sep - 3 oct): 2.0  -> sube 50 %
        filas = [_fila("2026-10-05", bruto=1.0), _fila("2026-10-09", bruto=2.0), _fila("2026-09-28", bruto=2.0)]
        c = gasto.comparar_periodos(filas, 7, self.HASTA)
        self.assertAlmostEqual(c["actual"]["bruto"], 3.0)
        self.assertAlmostEqual(c["anterior"]["bruto"], 2.0)
        self.assertAlmostEqual(gasto.variacion(c["actual"]["bruto"], c["anterior"]["bruto"]), 0.5)
        self.assertEqual((c["anterior_desde"], c["anterior_hasta"]), ("2026-09-27", "2026-10-03"))
        baja = gasto.comparar_periodos([_fila("2026-10-05", bruto=1.0), _fila("2026-09-28", bruto=4.0)], 7, self.HASTA)
        self.assertAlmostEqual(gasto.variacion(baja["actual"]["bruto"], baja["anterior"]["bruto"]), -0.75)

    def test_sin_datos_en_un_periodo_se_nota(self):
        c = gasto.comparar_periodos([_fila("2026-10-05")], 7, self.HASTA)
        self.assertTrue(c["actual_tiene_datos"] and not c["anterior_tiene_datos"])
        vacio = gasto.comparar_periodos([_fila("2026-08-01")], 7, self.HASTA)
        self.assertFalse(vacio["actual_tiene_datos"])

    def test_variacion_sin_base_no_inventa_un_porcentaje(self):
        self.assertIsNone(gasto.variacion(5.0, 0.0))
        self.assertIsNone(gasto.texto_variacion(5.0, 0.0))
        self.assertAlmostEqual(gasto.variacion(1.0, -2.0), 1.5)  # la base se toma en valor absoluto

    def test_texto_variacion(self):
        self.assertEqual(gasto.texto_variacion(3.0, 2.0), "+1.0000 USD (+50 %)")
        self.assertEqual(gasto.texto_variacion(1.0, 4.0), "-3.0000 USD (-75 %)")
        self.assertEqual(gasto.texto_variacion(2.0, 2.0), "+0.0000 USD (+0 %)")


class VistaMesesTests(unittest.TestCase):
    def test_meses_del_periodo_y_cambio_de_anio(self):
        from datetime import date

        self.assertEqual(gasto.meses_del_periodo(3, date(2026, 1, 15)), ["2025-11", "2025-12", "2026-01"])
        self.assertEqual(gasto.meses_del_periodo(1, date(2026, 10, 25)), ["2026-10"])
        self.assertEqual(len(gasto.meses_del_periodo(12, date(2026, 10, 25))), 12)
        self.assertEqual(gasto.mes_anterior("2026-01"), "2025-12")
        self.assertEqual(gasto.mes_anterior("2026-10"), "2026-09")

    def test_etiqueta_de_mes(self):
        self.assertEqual(gasto.etiqueta_de_mes("2026-09"), "sep 2026")
        self.assertEqual(gasto.etiqueta_de_mes("2027-01"), "ene 2027")

    def test_serie_mensual_suma_por_mes_pliega_otros_y_excluye_ajustes(self):
        filas = [_fila("2026-09-01", "Cloud Run", 1.0), _fila("2026-09-20", "Cloud Run", 2.0),
                 _fila("2026-09-05", "Cloud Logging", 0.5), _fila("2026-10-02", "Cloud Run", 4.0),
                 _fila("2026-09-05", "Invoice", -0.01)]
        datos = gasto.serie_mensual(filas, "bruto")
        sep = {d["servicio"]: d["monto"] for d in datos if d["periodo"] == "2026-09"}
        self.assertEqual(sep, {"Cloud Run": 3.0, "Otros": 0.5})
        self.assertNotIn("Invoice", {d["servicio"] for d in datos})
        self.assertTrue(all(d["monto"] >= 0 for d in datos))
        self.assertEqual({d["etiqueta"] for d in datos}, {"sep 2026", "oct 2026"})

    @staticmethod
    def _mes_completo(mes, bruto_por_dia, creditos_por_dia=0.0):
        from datetime import date

        n = gasto.dias_del_mes(mes)
        return [_fila(date(int(mes[:4]), int(mes[5:7]), d).isoformat(), bruto=bruto_por_dia, creditos=creditos_por_dia)
                for d in range(1, n + 1)]

    def test_resumen_mensual_compara_solo_meses_con_datos_de_casi_todos_sus_dias(self):
        filas = self._mes_completo("2026-08", 1.0, -0.5) + self._mes_completo("2026-09", 1.5, -0.5)
        r = {x["mes"]: x for x in gasto.resumen_mensual(filas)}
        self.assertEqual(r["2026-08"]["dias_con_datos"], 31)
        self.assertIsNone(r["2026-08"]["variacion_bruto"])  # primer mes: nada con que comparar
        self.assertAlmostEqual(r["2026-09"]["bruto"], 45.0)
        self.assertAlmostEqual(r["2026-09"]["variacion_bruto"], 45.0 / 31 - 1)  # 30 dias a 1.5 contra 31 a 1.0
        self.assertAlmostEqual(r["2026-09"]["variacion_promedio"], 0.5)  # 1.5 por dia contra 1.0 por dia: +50 %
        self.assertAlmostEqual(r["2026-09"]["neto"], 30.0)

    def test_un_mes_con_pocos_dias_no_se_compara_el_caso_real_que_daba_mas_1313_por_ciento(self):
        # agosto con 2 dias de datos, septiembre con 5: antes daba una variacion absurda
        filas = [_fila("2026-08-01", bruto=0.01), _fila("2026-08-31", bruto=0.04)] + \
                [_fila(f"2026-09-0{d}", bruto=0.14) for d in range(1, 6)]
        r = {x["mes"]: x for x in gasto.resumen_mensual(filas)}
        self.assertLess(r["2026-08"]["cobertura"], gasto.COBERTURA_MINIMA)
        self.assertIsNone(r["2026-09"]["variacion_bruto"])
        self.assertIsNone(r["2026-09"]["variacion_neto"])
        self.assertIsNone(r["2026-09"]["variacion_promedio"])  # el mes anterior tampoco es confiable

    def test_un_mes_a_medias_se_compara_por_promedio_diario_contra_un_mes_completo(self):
        filas = self._mes_completo("2026-09", 2.0) + [_fila(f"2026-10-{d:02d}", bruto=3.0) for d in range(1, 11)]
        r = {x["mes"]: x for x in gasto.resumen_mensual(filas)}
        self.assertIsNone(r["2026-10"]["variacion_bruto"])  # 10 dias contra 30: los totales no son comparables
        self.assertAlmostEqual(r["2026-10"]["variacion_promedio"], 0.5)  # 3.0 por dia contra 2.0 por dia
        self.assertEqual(r["2026-10"]["dias_con_datos"], 10)

    def test_si_el_mes_anterior_no_tiene_datos_no_se_compara_con_otro_mas_antiguo(self):
        filas = self._mes_completo("2026-08", 1.0) + self._mes_completo("2026-10", 1.0)  # septiembre sin datos
        r = {x["mes"]: x for x in gasto.resumen_mensual(filas)}
        self.assertIsNone(r["2026-10"]["variacion_promedio"])
        self.assertEqual(sorted(r), ["2026-08", "2026-10"])

    def test_dias_del_mes(self):
        self.assertEqual(gasto.dias_del_mes("2026-02"), 28)
        self.assertEqual(gasto.dias_del_mes("2028-02"), 29)
        self.assertEqual(gasto.dias_del_mes("2026-12"), 31)

    def test_es_parcial(self):
        from datetime import date

        hoy = date(2026, 10, 25)
        self.assertTrue(gasto.es_parcial("2026-10", "2026-10-24", hoy))  # el mes en curso
        self.assertTrue(gasto.es_parcial("2026-09", "2026-09-05", hoy))  # los datos se cortan el 5
        self.assertFalse(gasto.es_parcial("2026-09", "2026-09-30", hoy))  # llega hasta el 30
        self.assertFalse(gasto.es_parcial("2026-08", "2026-09-05", hoy))  # agosto está completo: hay datos posteriores


def _c(dia, servicio, sku, ubicacion="us-central1", bruto=1.0, creditos=0.0):
    f = _fila(dia, servicio, bruto, creditos)
    f.update(sku=sku, ubicacion=ubicacion)
    return f


class ConceptosTests(unittest.TestCase):
    def test_es_egreso_solo_para_salidas_de_datos(self):
        self.assertTrue(gasto.es_egreso("Network Internet Data Transfer Out from Americas to EMEA"))
        self.assertTrue(gasto.es_egreso("Network Data Transfer Out via Carrier Peering Network - Americas Based"))
        self.assertTrue(gasto.es_egreso("Egress from Europe to Americas"))
        self.assertFalse(gasto.es_egreso("Network Inter Region Data Transfer In from EMEA to Americas"))  # entrada, no egreso
        self.assertFalse(gasto.es_egreso("E2 Instance Core running in Americas"))
        self.assertFalse(gasto.es_egreso(None))

    def test_en_europa(self):
        self.assertTrue(gasto.en_europa("europe-west1"))
        self.assertTrue(gasto.en_europa("EU"))
        self.assertFalse(gasto.en_europa("us-central1"))
        self.assertFalse(gasto.en_europa("asia-east1"))
        self.assertFalse(gasto.en_europa(None))

    def test_tabla_por_concepto_agrupa_marca_y_dice_en_que_color_cae(self):
        filas = [
            _c("2026-09-01", "Compute Engine", "E2 Core", bruto=0.6), _c("2026-09-02", "Compute Engine", "E2 Core", bruto=0.6),
            _c("2026-09-01", "Cloud Logging", "Log Storage", bruto=0.02),
            _c("2026-09-01", "Cloud Storage", "Network Egress from Europe", ubicacion="europe-west1", bruto=0.3),
            _c("2026-09-01", "Invoice", "Rounding Error", ubicacion="-", bruto=-0.01),
        ]
        t = {c["sku"]: c for c in gasto.tabla_por_concepto(filas)}
        self.assertAlmostEqual(t["E2 Core"]["bruto"], 1.2)
        self.assertEqual(t["E2 Core"]["grupo"], "Compute Engine")
        self.assertEqual(t["Log Storage"]["grupo"], "Otros")  # lo que se pliega en "Otros" se ve aquí
        self.assertEqual(t["Rounding Error"]["grupo"], "(ajuste, no se grafica)")
        self.assertTrue(t["Network Egress from Europe"]["egreso"] and t["Network Egress from Europe"]["europa"])
        self.assertFalse(t["E2 Core"]["egreso"] or t["E2 Core"]["europa"])
        self.assertEqual([c["sku"] for c in gasto.tabla_por_concepto(filas)][0], "E2 Core")  # mayor valor primero

    def test_filas_sin_sku_ni_region_no_rompen(self):
        t = gasto.tabla_por_concepto([_fila("2026-09-01")])
        self.assertEqual((t[0]["sku"], t[0]["ubicacion"]), ("(sin SKU)", "-"))

    def test_cambios_ordena_del_que_mas_subio_al_que_mas_bajo(self):
        antes = [_c("2026-08-01", "Networking", "NIC", bruto=0.1), _c("2026-08-01", "Cloud Run", "Run", bruto=1.0)]
        ahora = [_c("2026-09-01", "Networking", "NIC", bruto=0.5), _c("2026-09-01", "Cloud Run", "Run", bruto=0.4),
                 _c("2026-09-01", "VM Manager", "VMM", bruto=0.3)]
        cambios = gasto.cambios_por_concepto(ahora, antes)
        self.assertEqual([c["sku"] for c in cambios], ["NIC", "VMM", "Run"])
        self.assertAlmostEqual(cambios[0]["diferencia"], 0.4)
        self.assertAlmostEqual(cambios[1]["antes"], 0.0)  # concepto nuevo: no existía antes
        self.assertAlmostEqual(cambios[2]["diferencia"], -0.6)

    def test_cambios_no_lista_lo_que_no_cambio(self):
        filas = [_c("2026-08-01", "Cloud Run", "Run", bruto=1.0)]
        self.assertEqual(gasto.cambios_por_concepto(filas, [_c("2026-09-01", "Cloud Run", "Run", bruto=1.0)]), [])

    def test_cambios_por_dia_compara_meses_con_distinta_cantidad_de_dias(self):
        # agosto: 2 dias a 1.0 (total 2.0); septiembre: 5 dias a 1.0 (total 5.0): por dia no cambio nada
        agosto = [_c(f"2026-08-0{d}", "Cloud Run", "Run", bruto=1.0) for d in (1, 2)]
        septiembre = [_c(f"2026-09-0{d}", "Cloud Run", "Run", bruto=1.0) for d in range(1, 6)]
        self.assertEqual(gasto.cambios_por_concepto(septiembre, agosto, por_dia=True), [])
        total = gasto.cambios_por_concepto(septiembre, agosto, por_dia=False)  # sin normalizar, engaña: parece que subio 3.0
        self.assertAlmostEqual(total[0]["diferencia"], 3.0)

    def test_cambios_marca_egreso_y_europa(self):
        ahora = [_c("2026-09-01", "Cloud Storage", "Data Transfer Out", ubicacion="europe-west4", bruto=2.0)]
        c = gasto.cambios_por_concepto(ahora, [])[0]
        self.assertTrue(c["egreso"] and c["europa"])

    def test_recortar_dias_con_retroceso_da_el_periodo_anterior(self):
        from datetime import date

        filas = [_fila("2026-10-05"), _fila("2026-09-30"), _fila("2026-09-27"), _fila("2026-09-26")]
        previo = gasto.recortar_dias(filas, 7, date(2026, 10, 10), retroceso=7)
        self.assertEqual([f["dia"] for f in previo], ["2026-09-30", "2026-09-27"])  # 27 sep a 3 oct


if __name__ == "__main__":
    unittest.main()
