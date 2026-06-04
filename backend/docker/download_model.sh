#!/bin/sh
# One-time model weight download from GCS to /model-weights/
# Runs at every Cloud Run cold start. Same-region GCS → Cloud Run egress is free.
# Download time: ~10-15s for 15GB AWQ model at in-region speeds.
set -e

MODEL_DIR="${MODEL_PATH:-/model-weights/gemma-4-26b-a4b-awq}"
GCS_PATH="${GCS_MODEL_WEIGHTS_PATH:-gs://rait-qa-model-weights/gemma-4-26b-a4b-awq}"

if [ -f "${MODEL_DIR}/config.json" ]; then
  echo "Model weights already present at ${MODEL_DIR}, skipping download."
  exit 0
fi

echo "Downloading model weights from ${GCS_PATH} to ${MODEL_DIR}..."
mkdir -p "${MODEL_DIR}"
gcloud storage cp --recursive "${GCS_PATH}/*" "${MODEL_DIR}/"
echo "Download complete."
