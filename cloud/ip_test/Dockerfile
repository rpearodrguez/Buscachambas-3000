# Contexto de build = raíz del repo (ver cloudbuild.yaml)
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir requests beautifulsoup4
COPY comun/ ./comun/
COPY escaner/ ./escaner/
COPY cloud/ip_test/ip_probe.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "ip_probe.py"]
