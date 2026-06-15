"""
GCS service — thin async wrapper around google-cloud-storage.
Used for DOM snapshots (per run) and model weight access.
Requires Application Default Credentials (set via gcloud auth application-default login).
"""
import json
import asyncio
import structlog
from app.config import get_settings

log = structlog.get_logger()

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from google.cloud import storage
        _client = storage.Client(project="rait-qa-agent")
        return _client
    except Exception as exc:
        log.warning("gcs.client_init_failed", error=str(exc)[:100])
        return None


async def upload_json(bucket_name: str, object_path: str, data: dict) -> str | None:
    """
    Upload a JSON-serialisable dict to GCS.
    Returns the gs:// URI on success, None on failure.
    """
    def _upload():
        client = _get_client()
        if not client:
            return None
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_path)
        blob.upload_from_string(
            json.dumps(data, default=str),
            content_type="application/json",
        )
        return f"gs://{bucket_name}/{object_path}"

    try:
        loop = asyncio.get_event_loop()
        uri = await loop.run_in_executor(None, _upload)
        if uri:
            log.info("gcs.upload.ok", uri=uri)
        return uri
    except Exception as exc:
        log.warning("gcs.upload.error", bucket=bucket_name, path=object_path, error=str(exc)[:100])
        return None


async def download_json(bucket_name: str, object_path: str) -> dict | None:
    """Download and deserialise a JSON object from GCS."""
    def _download():
        client = _get_client()
        if not client:
            return None
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())

    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _download)
    except Exception as exc:
        log.warning("gcs.download.error", bucket=bucket_name, path=object_path, error=str(exc)[:100])
        return None


def get_dom_snapshots_bucket() -> str:
    return get_settings().gcs_dom_snapshots_bucket
