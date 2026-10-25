# Imagen del scan que corre como Cloud Run Job. Contexto de build = raíz del
# repo (ver cloudbuild.yaml). Los argumentos del job son los de la CLI de
# scan_getonbrd.py (--pais, --storage, --solo, ...).
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir requests beautifulsoup4 google-cloud-storage \
    && useradd --no-create-home --uid 10001 scanner
COPY scan_getonbrd.py extraer_oferta.py almacen.py ./
USER scanner
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "scan_getonbrd.py"]
