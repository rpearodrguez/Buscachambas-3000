# Imagen del scan que corre como Cloud Run Job. Contexto de build = raíz del
# repo (ver cloudbuild.yaml). Los argumentos del job son los de la CLI de
# escaner/scan_getonbrd.py (--pais, --storage, --solo, ...).
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir requests beautifulsoup4 google-cloud-storage \
    && useradd --no-create-home --uid 10001 scanner
COPY comun/ ./comun/
COPY escaner/ ./escaner/
USER scanner
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "escaner/scan_getonbrd.py"]
