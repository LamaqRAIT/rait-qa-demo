FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake Qwen3-Embedding-0.6B weights into the image so they're available offline
# at runtime (HF_HUB_OFFLINE=1 reads from this cache, never hits HF servers).
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('Qwen/Qwen3-Embedding-0.6B', trust_remote_code=True); \
print('Qwen3-Embedding-0.6B baked into image')"

COPY . .

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
