# syntax=docker/dockerfile:1
# CarbonMG — imagem de produção
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CARBONO_MODO=producao \
    CARBONO_DB=/dados/carbono.db \
    CARBONO_ANEXOS=/dados/anexos

WORKDIR /app
COPY requirements.txt .
# Rede com proxy que inspeciona TLS (comum em órgãos públicos)? Passe a CA do proxy só na construção:
#   docker build --secret id=ca,src=/caminho/ca-do-proxy.crt --build-arg HTTPS_PROXY=... .
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 --home /app carbonmg \
 && mkdir -p /dados /backups && chown carbonmg /dados /backups

COPY plataforma ./plataforma
USER carbonmg
VOLUME ["/dados", "/backups"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/saude', timeout=4).status == 200 else 1)"

# O proxy reverso (TLS) deve ficar na frente; informe o IP dele em CARBONO_PROXY_IPS.
CMD ["python", "-m", "plataforma", "servir", "--host", "0.0.0.0", "--porta", "8000"]
