FROM python:3.11-slim AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf-cache

RUN python -m venv "${VIRTUAL_ENV}"

WORKDIR /build
COPY requirements.txt .
# Use CPU-only PyTorch in Linux containers; local macOS installs can use MPS.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch==2.10.0+cpu" \
    && pip install --no-cache-dir -r requirements.txt

# Cache the embedding model in the image so the app does not download it at startup.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/home/appuser/.cache/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --create-home \
        --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/chroma_db "${HF_HOME}" \
    && chown -R 10001:10001 /app /home/appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=10001:10001 /opt/hf-cache/ /home/appuser/.cache/huggingface/
COPY --chown=10001:10001 app.py rag_engine.py ./
COPY --chown=10001:10001 runbooks/ ./runbooks/

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
