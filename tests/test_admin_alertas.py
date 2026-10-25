"""Tests de la configuración del correo del administrador en el job (objetos reales de run_v2, sin red).
    python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.cloud import run_v2

from administracion import admin_nube

CFG = {"project": "mi-proyecto-123", "region": "us-east1", "job": "mi-job", "bucket": "b", "secret": "s"}


def job_con(env):
    """Un Job real con esas variables; la de la contraseña viene de un secreto, como en el job de verdad."""
    contenedor = run_v2.Container(image="imagen", env=[run_v2.EnvVar(name=n, value=v) for n, v in env.items()])
    contenedor.env.append(run_v2.EnvVar(
        name="BUSCACHAMBAS_SMTP_PASSWORD",
        value_source=run_v2.EnvVarSource(secret_key_ref=run_v2.SecretKeySelector(secret="el-secreto", version="latest"))))
    return run_v2.Job(template=run_v2.ExecutionTemplate(template=run_v2.TaskTemplate(containers=[contenedor])))


class ClienteFalso:
    def __init__(self, job):
        self.job = job
        self.actualizaciones = 0

    def get_job(self, name):
        self.pedido = name
        return self.job

    def update_job(self, job):
        self.actualizaciones += 1

        class Operacion:
            def result(self, timeout=None):
                return None
        return Operacion()


def variables(job):
    return {e.name: (e.value or "<secreto>") for e in job.template.template.containers[0].env}


class AdminEmailEnElJobTests(unittest.TestCase):
    BASE = {"BUSCACHAMBAS_SMTP_HOST": "h.cl", "BUSCACHAMBAS_SMTP_PORT": "587", "BUSCACHAMBAS_SMTP_USER": "u@x.cl", "OTRA_VARIABLE": "se queda"}

    def test_leer_incluye_el_correo_del_administrador(self):
        leido = admin_nube.leer_smtp_del_job(CFG, cliente=ClienteFalso(job_con({**self.BASE, "BUSCACHAMBAS_ADMIN_EMAIL": "jefe@x.cl"})))
        self.assertEqual(leido["admin_email"], "jefe@x.cl")
        self.assertEqual((leido["host"], leido["usuario"]), ("h.cl", "u@x.cl"))

    def test_sin_el_correo_configurado_lee_vacio(self):
        self.assertEqual(admin_nube.leer_smtp_del_job(CFG, cliente=ClienteFalso(job_con(self.BASE)))["admin_email"], "")

    def test_agrega_el_correo_del_administrador_sin_tocar_nada_mas(self):
        cliente = ClienteFalso(job_con(self.BASE))
        admin_nube.actualizar_smtp_job(CFG, "h.cl", 587, "u@x.cl", admin_email="  jefe@x.cl ", cliente=cliente)
        v = variables(cliente.job)
        self.assertEqual(v["BUSCACHAMBAS_ADMIN_EMAIL"], "jefe@x.cl")
        self.assertEqual(v["OTRA_VARIABLE"], "se queda")
        self.assertEqual(v["BUSCACHAMBAS_SMTP_PASSWORD"], "<secreto>")  # el secreto sigue mapeado
        self.assertEqual(cliente.actualizaciones, 1)

    def test_cambiar_el_correo_reemplaza_el_valor_sin_duplicar(self):
        cliente = ClienteFalso(job_con({**self.BASE, "BUSCACHAMBAS_ADMIN_EMAIL": "viejo@x.cl"}))
        admin_nube.actualizar_smtp_job(CFG, "h.cl", 587, "u@x.cl", admin_email="nuevo@x.cl", cliente=cliente)
        nombres = [e.name for e in cliente.job.template.template.containers[0].env]
        self.assertEqual(nombres.count("BUSCACHAMBAS_ADMIN_EMAIL"), 1)
        self.assertEqual(variables(cliente.job)["BUSCACHAMBAS_ADMIN_EMAIL"], "nuevo@x.cl")

    def test_vaciarlo_quita_la_variable_y_no_deja_una_vacia(self):
        cliente = ClienteFalso(job_con({**self.BASE, "BUSCACHAMBAS_ADMIN_EMAIL": "jefe@x.cl"}))
        admin_nube.actualizar_smtp_job(CFG, "h.cl", 587, "u@x.cl", admin_email="", cliente=cliente)
        v = variables(cliente.job)
        self.assertNotIn("BUSCACHAMBAS_ADMIN_EMAIL", v)
        self.assertEqual((v["OTRA_VARIABLE"], v["BUSCACHAMBAS_SMTP_PASSWORD"]), ("se queda", "<secreto>"))

    def test_vaciarlo_cuando_no_existia_no_hace_nada_raro(self):
        cliente = ClienteFalso(job_con(self.BASE))
        admin_nube.actualizar_smtp_job(CFG, "h.cl", 587, "u@x.cl", admin_email="", cliente=cliente)
        self.assertNotIn("BUSCACHAMBAS_ADMIN_EMAIL", variables(cliente.job))

    def test_none_deja_el_correo_del_administrador_como_estaba(self):
        cliente = ClienteFalso(job_con({**self.BASE, "BUSCACHAMBAS_ADMIN_EMAIL": "jefe@x.cl"}))
        admin_nube.actualizar_smtp_job(CFG, "otro.cl", 465, "u@x.cl", cliente=cliente)
        v = variables(cliente.job)
        self.assertEqual((v["BUSCACHAMBAS_ADMIN_EMAIL"], v["BUSCACHAMBAS_SMTP_HOST"], v["BUSCACHAMBAS_SMTP_PORT"]), ("jefe@x.cl", "otro.cl", "465"))

    def test_usa_el_job_de_la_conexion(self):
        cliente = ClienteFalso(job_con(self.BASE))
        admin_nube.leer_smtp_del_job(CFG, cliente=cliente)
        self.assertEqual(cliente.pedido, "projects/mi-proyecto-123/locations/us-east1/jobs/mi-job")


if __name__ == "__main__":
    unittest.main()
