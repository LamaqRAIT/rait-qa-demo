# RAIT QA Agent — Handoff Document

**Date:** 6 June 2026  
**Author:** Vedant  
**Recipient:** Lamaq (`lamaqraitlabs@gmail.com`)  
**Branch:** `main` — all code is merged; 2 commits ahead of remote (unpushed, see §Pending Pushes)  
**Live service:** `https://rait-qa-backend-1097873447958.us-central1.run.app`

---

## 1. What Has Been Completed

The full architectural rework from `references/13_rework_planning_and_decisions.md` (§10.4 build order, 14 steps) is **100% implemented in code**. Every step is confirmed against the actual files, not the plan.

### Step-by-step status

| # | Step | Status | Evidence file |
|---|------|--------|---------------|
| 1 | Fix `test_history` injection | ✅ Done | `app/core/evidence.py` — `build_test_history()` queries DB for last 30 days |
| 2 | Call `store_failure_pattern()` after auto-fix | ✅ Done | `app/core/pipeline.py` — called in `_apply_fix_and_complete()` |
| 3 | DB migration: 13 new gate/latency/storage columns | ✅ Done | `app/db.py` — `ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS` for all 13 |
| 4 | GCS DOM snapshot persistence | ✅ Done | `app/nodes/inspector.py` + `app/services/gcs.py` |
| 5 | GCP infrastructure provisioned | ✅ Done | Cloud Run CPU live, Cloud SQL RUNNABLE, GCS buckets, Artifact Registry — all in `rait-qa-agent` |
| 6 | DeBERTa NLI in-process (`cross-encoder/nli-deberta-v3-small`) | ✅ Done | `app/services/nli.py` — singleton, loaded at startup |
| 7 | Qwen3-Embedding-0.6B in-process | ✅ Done | `app/services/embedding.py` — singleton, catalogue pre-computed at startup |
| 8 | Two-step vLLM triage (Step A: 1 token + logprobs; Step B: guided JSON) | ✅ Done | `app/nodes/triage.py` + `app/llm/vllm_client.py` |
| 9 | 5-signal confidence gate | ✅ Done | `app/core/confidence_gate.py` — p_class≥0.75, margin≥0.15, NLI≥0.50, fix_grounded, dom_corr≥0.60 |
| 10 | Suite selector: embedding cosine similarity replaces LLM call | ✅ Done | `app/core/suite_selector.py` — Tier 2 is now Qwen3 embeddings, threshold 0.35 |
| 11 | Deterministic reporter (no LLM call) | ✅ Done | `app/nodes/reporter.py` — 4-branch template, no Haiku call |
| 12 | OTel instrumentation | ✅ Done | `app/telemetry.py` — FastAPI + httpx auto-instrumentation + manual spans for NLI, gate, embedding |
| 13 | Shadow phase | ⏳ Blocked | Needs GPU service live first (blocker: quota) |
| 14 | Production flip / cleanup | ⏳ Blocked | Depends on shadow phase |

### Architecture summary (what was replaced)

| Component | Before | After |
|---|---|---|
| Triage | Single Groq/Claude/Gemini call | Two-step vLLM: logprobs (Step A) + guided JSON (Step B) |
| Confidence | Model-generated number 0–1 | 5 independent signals measured externally |
| Suite selector | LLM call | Qwen3-Embedding-0.6B cosine similarity (deterministic) |
| Reporter | Claude Haiku LLM call | Deterministic 4-branch template |
| Managed APIs | Groq + Gemini + Claude + Langfuse | **All removed.** Only self-hosted vLLM on GCP |
| Hosting | Railway | GCP Cloud Run (CPU + GPU) + Cloud SQL + GCS |
| Observability | Langfuse | OTel → Cloud Trace + Cloud Monitoring + Cloud Logging |

---

## 2. GCP Infrastructure (already provisioned)

All resources are in project **`rait-qa-agent`**, region **`us-central1`**:

| Resource | Status | Notes |
|---|---|---|
| Cloud Run CPU (`rait-qa-backend`) | ✅ Live, revision `00005-clf` | CPU service with Cloud SQL proxy attached |
| Cloud SQL (`rait-qa-db`) | ✅ RUNNABLE | PostgreSQL 16, db-f1-micro. DB: `rait_qa`, user: `rait_app` |
| GCS (`rait-qa-model-weights`) | ✅ | Model weights for GPU service |
| GCS (`rait-qa-dom-snapshots`) | ✅ | Per-run DOM snapshots |
| Artifact Registry (`rait-qa-images`) | ✅ | `us-central1-docker.pkg.dev/rait-qa-agent/rait-qa-images` |
| Secret Manager | ✅ 5 secrets exist | `db-url` + `hf-token` populated; 3 have placeholder values (see §4) |
| Cloud Build trigger | ✅ Configured | `cloudbuild.yaml` auto-deploys CPU service on push to `main` |
| Cloud Run GPU (`rait-qa-vllm`) | ❌ Not deployed | **Blocked: GPU quota pending** |

---

## 3. Blockers

### Blocker 1 (Critical) — GPU quota not yet approved

The L4 GPU quota request for Cloud Run in us-central1 was submitted by Vedant on 4 June 2026. GCP approval takes 1–2 business days. No action needed from Lamaq on this; wait for the approval email to Vedant's account, then proceed to §4 Task 3.

Until GPU quota is approved:
- The backend runs in deterministic-fallback mode: vLLM client returns `None`, triage falls back to `_deterministic_triage_extended()`, confidence gate unconditionally routes to `human_review`
- Full HITL flow works (submit failures → human review → approve → PR)
- Auto-fix path does NOT work (requires GPU for logprobs + guided JSON)

### Blocker 2 (Critical) — Model weights mismatch

The model download Cloud Run Job (`download-model-weights`, execution `z86tf`) was downloading `Qwen/Qwen2.5-7B-Instruct` into `gs://rait-qa-model-weights/qwen2.5-7b-instruct/`. However, the GPU service configuration (`docker/Dockerfile.gpu` + `docker/download_model.sh`) targets `google/gemma-4-26b-a4b` AWQ at `gs://rait-qa-model-weights/gemma-4-26b-a4b-awq/`.

**This means the weights bucket has the wrong model.** Before deploying the GPU service:

1. Accept the Gemma 4 license at `huggingface.co/google/gemma-4-26b-a4b` (requires Google login)
2. Ensure `rait-qa-hf-token` secret has a HuggingFace token that has access to `google/gemma-4-26b-a4b`
3. Trigger a new download job with the correct model (instructions in §4 Task 3)

### Blocker 3 (Minor) — Three secrets have placeholder values

`rait-qa-vllm-url`, `rait-qa-github-token`, and `rait-qa-slack-webhook` were created with placeholder strings. Real values needed before CI/CD pipeline and Slack notifications work. See §4 Tasks 1 and 2.

### Blocker 4 (Minor) — GitHub Actions workflow file needs push

`.github/workflows/deploy-gcp.yml` exists locally and in `cloudbuild.yaml`, but the workflow file couldn't be pushed to GitHub because the PAT lacked `workflow` scope. The CI/CD pipeline does auto-trigger on Cloud Build (via `cloudbuild.yaml`) already — this is only needed for the GitHub Actions path. See §4 Task 1.

---

## 4. What Lamaq Needs to Do

The tasks are ordered by dependency. **Tasks 1 and 2 can be done immediately.** Task 3 requires GPU quota to arrive first.

---

### Task 1 — Wire up GitHub Actions CI/CD (15 min)

The `deploy-gcp.yml` workflow uses a GCP service account key stored as a GitHub secret `GCP_SA_KEY`.

**Step 1:** Generate the service account key (run on Vedant's machine or your own with the `rait-dev` gcloud config):

```bash
gcloud iam service-accounts keys create /tmp/cicd-key.json \
  --iam-account=rait-qa-cicd@rait-qa-agent.iam.gserviceaccount.com \
  --project=rait-qa-agent \
  --configuration=rait-dev

# Base64-encode it (no newlines)
cat /tmp/cicd-key.json | base64 | tr -d '\n'
# Copy the output — this is your GCP_SA_KEY value
rm /tmp/cicd-key.json
```

**Step 2:** Add it as a GitHub repository secret:
- Go to `github.com/LamaqRAIT/rait-qa-demo` → Settings → Secrets and variables → Actions
- New repository secret: name `GCP_SA_KEY`, value = the base64 string from Step 1

**Step 3:** Push the workflow file (requires a PAT with `workflow` scope):
- Go to GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
- Create token with `repo` + `workflow` permissions on `LamaqRAIT/rait-qa-demo`
- Then:

```bash
cd rait-qa-demo
git remote set-url origin https://YOUR_WORKFLOW_PAT@github.com/LamaqRAIT/rait-qa-demo.git
git push origin main
```

This also pushes the 2 pending commits (`5261efa` cloudbuild.yaml, `c197743` handoff update) that are ahead of remote.

---

### Task 2 — Replace placeholder secrets (10 min)

The `rait-qa-github-token` and `rait-qa-slack-webhook` secrets currently hold placeholder strings. Update them with real values:

```bash
# GitHub token (PAT with repo + workflow scope)
printf 'ghp_YOUR_REAL_TOKEN' | gcloud secrets versions add rait-qa-github-token \
  --data-file=- --project=rait-qa-agent --configuration=rait-dev

# Slack webhook (skip if you don't want Slack notifications — the backend handles absence gracefully)
printf 'https://hooks.slack.com/services/YOUR/REAL/WEBHOOK' | gcloud secrets versions add rait-qa-slack-webhook \
  --data-file=- --project=rait-qa-agent --configuration=rait-dev
```

`rait-qa-db-url` and `rait-qa-hf-token` already have correct values.

---

### Task 3 — Deploy GPU service (after quota email arrives)

This is the most involved task. Follow these steps in order.

#### 3a. Accept Gemma 4 license
Go to `https://huggingface.co/google/gemma-4-26b-a4b` and accept the access agreement with the Google account linked to your HuggingFace token. This is required once per model, per HF account.

Verify the HF token stored in `rait-qa-hf-token` has access to this model:
```bash
# Quick check — should print model metadata, not a 403
HF_TOKEN=$(gcloud secrets versions access latest \
  --secret=rait-qa-hf-token --project=rait-qa-agent --configuration=rait-dev)
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $HF_TOKEN" \
  "https://huggingface.co/api/models/google/gemma-4-26b-a4b"
# Expected: 200
```

If it returns 401 or 403, the token doesn't have access — get a new HF token or accept the license with a different account.

#### 3b. Download Gemma 4 weights to GCS

The existing download job has wrong model config. Create a new one-shot download job:

```bash
gcloud run jobs create download-gemma4-weights \
  --image=us-central1-docker.pkg.dev/rait-qa-agent/rait-qa-images/backend:latest \
  --region=us-central1 \
  --project=rait-qa-agent \
  --service-account=rait-qa-backend@rait-qa-agent.iam.gserviceaccount.com \
  --memory=16Gi --cpu=4 \
  --task-timeout=3600 \
  --set-env-vars="HF_MODEL=google/gemma-4-26b-a4b,GCS_DEST=gs://rait-qa-model-weights/gemma-4-26b-a4b-awq" \
  --update-secrets="HF_TOKEN=rait-qa-hf-token:latest" \
  --command="python" \
  --args="-c,import os; from huggingface_hub import snapshot_download; import subprocess; snapshot_download('google/gemma-4-26b-a4b', token=os.getenv('HF_TOKEN'), local_dir='/tmp/model'); subprocess.run(['gcloud', 'storage', 'cp', '-r', '/tmp/model/.', os.getenv('GCS_DEST')], check=True)" \
  --configuration=rait-dev

gcloud run jobs execute download-gemma4-weights \
  --region=us-central1 --project=rait-qa-agent --configuration=rait-dev
```

Monitor with:
```bash
gcloud run jobs executions list --job=download-gemma4-weights \
  --region=us-central1 --project=rait-qa-agent --configuration=rait-dev
```

Wait for status `SUCCEEDED` (takes ~20–30 min, ~15GB download). Verify:
```bash
gcloud storage ls gs://rait-qa-model-weights/gemma-4-26b-a4b-awq/
# Should show config.json, model-*.safetensors, tokenizer files
```

#### 3c. Deploy the GPU Cloud Run service

```bash
# Build and push the GPU Dockerfile first
cd rait-qa-demo/backend
docker build -f docker/Dockerfile.gpu \
  -t us-central1-docker.pkg.dev/rait-qa-agent/rait-qa-images/vllm-backend:latest .
docker push us-central1-docker.pkg.dev/rait-qa-agent/rait-qa-images/vllm-backend:latest

# Deploy
gcloud run deploy rait-qa-vllm \
  --image=us-central1-docker.pkg.dev/rait-qa-agent/rait-qa-images/vllm-backend:latest \
  --region=us-central1 \
  --gpu=1 --gpu-type=nvidia-l4 \
  --memory=24Gi --cpu=8 \
  --min-instances=0 --max-instances=1 \
  --timeout=300 \
  --no-allow-unauthenticated \
  --service-account=rait-qa-backend@rait-qa-agent.iam.gserviceaccount.com \
  --set-env-vars="MODEL_PATH=/model-weights/gemma-4-26b-a4b-awq,GCS_MODEL_WEIGHTS_PATH=gs://rait-qa-model-weights/gemma-4-26b-a4b-awq,OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317" \
  --project=rait-qa-agent \
  --configuration=rait-dev
```

#### 3d. Update the vLLM URL secret

```bash
VLLM_URL=$(gcloud run services describe rait-qa-vllm \
  --region=us-central1 --project=rait-qa-agent \
  --format="value(status.url)" --configuration=rait-dev)

printf "$VLLM_URL" | gcloud secrets versions add rait-qa-vllm-url \
  --data-file=- --project=rait-qa-agent --configuration=rait-dev

echo "vLLM URL: $VLLM_URL"
```

Then force a new revision of the CPU service to pick up the updated secret:
```bash
gcloud run services update rait-qa-backend \
  --update-secrets="VLLM_BASE_URL=rait-qa-vllm-url:latest" \
  --region=us-central1 --project=rait-qa-agent --configuration=rait-dev
```

#### 3e. Verify GPU service is working

```bash
# Health check
curl "${VLLM_URL}/health"   # Should return 200

# Quick classify test (Step A — 1 token + logprobs)
curl -X POST "${VLLM_URL}/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{"model": "google/gemma-4-26b-a4b", "prompt": "Classify: drift", "max_tokens": 1, "logprobs": true}'
# Should return logprobs with token probabilities
```

---

### Task 4 — Shadow phase (5–10 runs)

Once the GPU service is live, run 5–10 test pipeline runs before relying on auto-fix. This verifies:
- vLLM logprobs are non-null (p_class and margin correctly computed)
- Gate signals stored in DB (`p_class`, `logprob_margin`, `nli_entailment` columns non-null)
- OTel traces appearing in Cloud Trace dashboard

Check DB after a run:
```sql
SELECT id, p_class, logprob_margin, nli_entailment, fix_grounded, dom_corroboration, gate_route
FROM qa_runs ORDER BY created_at DESC LIMIT 5;
```

If all 5 gate signals are populated and `gate_route` is non-null, the pipeline is operating correctly.

---

### Task 5 — Pause Cloud SQL when not working (saves ~£7/month)

```bash
# Pause (stop billing when not actively developing)
gcloud sql instances patch rait-qa-db \
  --activation-policy=NEVER --project=rait-qa-agent --configuration=rait-dev

# Resume before working
gcloud sql instances patch rait-qa-db \
  --activation-policy=ALWAYS --project=rait-qa-agent --configuration=rait-dev
```

---

### Task 6 — Vercel frontends (when ready to go live)

- `agent-ui/` → deploy to Vercel, set `NEXT_PUBLIC_API_URL=https://rait-qa-backend-1097873447958.us-central1.run.app`
- `demo-site/` → deploy to Vercel (static site, no env vars needed)
- Decommission Railway services after Vercel is confirmed working

---

## 5. Decoupled Task Assignment

Short answer: **minimal true parallelism** — most of the remaining work is a single sequential dependency chain. Here's how to split it:

### Lamaq does (independent, can start now):
- Task 1: GitHub Actions setup + GCP_SA_KEY secret
- Task 2: Replace placeholder secrets (real GitHub PAT, Slack webhook)
- Monitoring model download job outcome and kicking off Gemma 4 download (Task 3a–3b) — this only needs GCP access, can happen in parallel with Vedant

### Vedant does (GCP-side, in parallel with Lamaq):
- Monitor GPU quota approval email (nothing to do, just wait)
- Verify Cloud Build is triggering correctly after workflow file push
- Add OTel Collector sidecar to CPU service (not yet done — `docker/otel-collector-config.yaml` exists but needs wiring into the Cloud Run service configuration)

### Both together (sequential, after GPU quota):
- Task 3c–3e: GPU service deployment and verification
- Task 4: Shadow phase — review Cloud Trace output together and confirm signals look right
- Production flip (mark shadow phase complete, update wiki)

### OTel sidecar (Vedant's task, can be done now while waiting for GPU):

The OTel Collector config exists at `backend/docker/otel-collector-config.yaml` but the CPU service isn't running the sidecar yet. To add it:

```bash
# Build and push the OTel Collector image
docker build -f docker/Dockerfile.otelcollector \
  -t us-central1-docker.pkg.dev/rait-qa-agent/rait-qa-images/otel-collector:latest .
docker push us-central1-docker.pkg.dev/rait-qa-agent/rait-qa-images/otel-collector:latest

# Update CPU service to run OTel sidecar
gcloud run services update rait-qa-backend \
  --container=otel-collector \
  --image=us-central1-docker.pkg.dev/rait-qa-agent/rait-qa-images/otel-collector:latest \
  --port=4317 \
  --region=us-central1 --project=rait-qa-agent --configuration=rait-dev
```

Note: Cloud Run multi-container (sidecars) is in GA. Docs: https://cloud.google.com/run/docs/deploying#sidecars

---

## 6. Monitoring & Health Checks

| What | Where |
|---|---|
| CPU service logs | `https://console.cloud.google.com/run/detail/us-central1/rait-qa-backend/logs?project=rait-qa-agent` |
| Cloud Build deploys | `https://console.cloud.google.com/cloud-build/builds?project=rait-qa-agent` |
| Cloud Trace (OTel) | `https://console.cloud.google.com/traces/list?project=rait-qa-agent` |
| Backend health | `https://rait-qa-backend-1097873447958.us-central1.run.app/health` |
| DB query (gate signals) | `SELECT p_class, gate_route, triage_ttft_ms FROM qa_runs ORDER BY created_at DESC LIMIT 10` |
| Model download job | `gcloud run jobs executions list --job=download-model-weights --region=us-central1 --project=rait-qa-agent --configuration=rait-dev` |

---

## 7. Cost (current phase)

~**£10–13/month** total against the $300 GCP credit (~23 months runway).

| Service | Cost |
|---|---|
| Cloud Run CPU | ~$0 (free tier handles dev volume) |
| Cloud Run GPU | ~$3/month once live (billed per 45s call, min=0) |
| Cloud SQL | ~$9.36/month — **pause when not working to save £7** |
| GCS (total) | ~$0.30/month |
| Artifact Registry | ~$0.10/month |

---

## 8. Pending Git Commits (unpushed)

Two commits are ahead of `github.com/LamaqRAIT/rait-qa-demo` remote:

```
c197743  docs: update Cloud Run URL and Cloud SQL proxy note in handoff
5261efa  ci: add full gcloud run deploy flags to cloudbuild.yaml
```

These go out as part of Task 1 (when the PAT with `workflow` scope is set and `git push origin main` is run).

---

*Code: `github.com/LamaqRAIT/rait-qa-demo`. Rework commit: `ffa777e`. All architecture decisions documented in `references/13_rework_planning_and_decisions.md`.*
