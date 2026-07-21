# Build enxuto: instala dependências numa camada, copia só o necessário na
# imagem final — imagem menor, menos superfície de ataque, deploy mais rápido
# no free tier (menos MB pra fazer upload/pull a cada deploy).
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim

# Usuário não-root: se a aplicação for comprometida por alguma dependência
# vulnerável, o processo não roda como root dentro do container.
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

COPY --from=builder /root/.local /home/appuser/.local
COPY app ./app

RUN chown -R appuser:appuser /app
USER appuser
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/healthz', timeout=3)" || exit 1

# Render/Fly/HF Spaces injetam PORT via env; shell form pra expandir a var.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
