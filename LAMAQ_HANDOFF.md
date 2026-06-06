# Handoff: Architecture Rework v2 — For Lamaq

**Date:** June 2026  
**Author:** Vedant (via Claude Code agent)  
**Branch:** `main` (all code merged)

---

## What Was Done

The full architectural rework from `references/13_rework_planning_and_decisions.md` has been implemented and is live on `main`. Every item in the build order (§10.4) has been addressed.

**Summary of changes:**

| Component | Before | After |
|---|---|---|
| Triage | Single Groq/Claude/Gemini call | Two-step vLLM: Step A (1 token + logprobs), Step B (guided JSON fix) |
| Confidence | Model-generated number (0.0–1.0) | 5-signal gate: p_class, margin, NLI, fix groundedness, DOM corroboration |
| Suite selector | LLM call | Qwen3-Embedding-0.6B cosine similarity (deterministic) |
| Reporter | Claude Haiku LLM call | Deterministic template with gate signal summary |
| Managed APIs | Groq + Gemini + Claude + Langfuse | **All removed.** Only vLLM self-hosted on GCP |
| Hosting | Railway | GCP Cloud Run (CPU + GPU) + Cloud SQL + GCS |
| Observability | Langfuse only | OTel → Cloud Trace + Cloud Monitoring + Cloud Logging |

---

## GCP Infrastructure (already created)

All in project `rait-qa-agent`:

| Resource | Status | Details |
|---|---|---|
| Cloud Run CPU (`rait-qa-backend`) | ✅ Live | `https://rait-qa-backend-tqmp6uu44a-uc.a.run.app` |
| Cloud SQL (`rait-qa-db`) | ✅ RUNNABLE | PostgreSQL 16, db-f1-micro, us-central1, DB: `rait_qa`, user: `rait_app` |
| GCS bucket (`rait-qa-model-weights`) | ✅ | Model weights storage |
| GCS bucket (`rait-qa-dom-snapshots`) | ✅ | Per-run DOM snapshots for replay/fine-tuning |
| Artifact Registry (`rait-qa-images`) | ✅ | `us-central1-docker.pkg.dev/rait-qa-agent/rait-qa-images` |
| Secret Manager | ✅ | `rait-qa-db-url`, `rait-qa-hf-token`, `rait-qa-vllm-url`, `rait-qa-github-token`, `rait-qa-slack-webhook` |
| Cloud Run GPU (`rait-qa-vllm`) | ⏳ | **Quota pending** (submitted by Vedant, 1–2 business days) |

---

## Things You (Lamaq) Need to Do

### 1. Add GitHub Actions workflow (5 min)
The CI/CD workflow `deploy-gcp.yml` was created locally but couldn't be pushed because the PAT doesn't have `workflow` scope.

The file is at `rait-qa-demo/.github/workflows/deploy-gcp.yml` on the local machine (Vedant's). Two options:

**Option A — Create a PAT with `workflow` scope:**
```
github.com → Settings → Developer settings → Personal access tokens → Generate new token
Scopes: repo + workflow
Replace the remote URL:
  cd rait-qa-demo
  git remote set-url origin https://YOUR_TOKEN@github.com/LamaqRAIT/rait-qa-demo.git
  git push origin main
```

**Option B — Add via GitHub UI:**
- Go to `github.com/LamaqRAIT/rait-qa-demo`
- Click **Add file → Create new file**
- Path: `.github/workflows/deploy-gcp.yml`
- Paste the content from [the file on Vedant's machine]

Once the workflow exists, every push to `main` auto-builds and deploys the backend.

### 2. Set secret values (10 min)
The Secret Manager secrets exist but need their values populated. For any that aren't set:

```bash
# GITHUB_TOKEN — your GitHub PAT (the one in the repo remote URL)
printf 'ghp_...' | gcloud secrets versions add rait-qa-github-token \
  --data-file=- --configuration=rait-dev

# SLACK_WEBHOOK_URL — if you want Slack notifications
printf 'https://hooks.slack.com/services/...' | gcloud secrets versions add rait-qa-slack-webhook \
  --data-file=- --configuration=rait-dev
```

`rait-qa-db-url` and `rait-qa-hf-token` are already populated.

### 3. Deploy GPU service when quota is approved
Once Vedant receives the GPU quota approval email:

```bash
gcloud run deploy rait-qa-vllm \
  --image=vllm/vllm-openai:gemma4 \
  --region=us-central1 \
  --gpu=1 --gpu-type=nvidia-l4 \
  --memory=16Gi --cpu=4 \
  --min-instances=0 --max-instances=1 \
  --timeout=300 \
  --service-account=rait-qa-backend@rait-qa-agent.iam.gserviceaccount.com \
  --set-env-vars="MODEL_PATH=/model-weights/qwen2.5-7b-instruct,GCS_MODEL_WEIGHTS_PATH=gs://rait-qa-model-weights/qwen2.5-7b-instruct" \
  --allow-unauthenticated \
  --project=rait-qa-agent \
  --configuration=rait-dev
```

Then store the service URL in Secret Manager:
```bash
VLLM_URL=$(gcloud run services describe rait-qa-vllm \
  --region=us-central1 --project=rait-qa-agent \
  --format="value(status.url)")
printf "$VLLM_URL" | gcloud secrets versions add rait-qa-vllm-url \
  --data-file=- --configuration=rait-dev
```

Then update the CPU service to read the secret:
```bash
gcloud run services update rait-qa-backend \
  --update-secrets="VLLM_BASE_URL=rait-qa-vllm-url:latest" \
  --region=us-central1 --project=rait-qa-agent --configuration=rait-dev
```

### 4. Pause Cloud SQL when not working (saves ~$7/month)
```bash
# Pause
gcloud sql instances patch rait-qa-db \
  --activation-policy=NEVER --configuration=rait-dev

# Resume  
gcloud sql instances patch rait-qa-db \
  --activation-policy=ALWAYS --configuration=rait-dev
```

### 5. Vercel for frontends (optional, when ready to go live)
- `agent-ui/` → deploy to Vercel, set `NEXT_PUBLIC_API_URL=https://rait-qa-backend-tqmp6uu44a-uc.a.run.app`
- `demo-site/` → deploy to Vercel (static, no env vars needed)
- Decommission Railway services after Vercel is confirmed working

---

## How to Monitor

**Cloud Build (backend deploys):**
```
https://console.cloud.google.com/cloud-build/builds?project=rait-qa-agent
```

**Cloud Run services:**
```
https://console.cloud.google.com/run?project=rait-qa-agent
```

**Cloud Trace (OTel distributed traces — once GPU is live):**
```
https://console.cloud.google.com/traces/list?project=rait-qa-agent
```

**Model download job status:**
```bash
gcloud run jobs executions list --job=download-model-weights \
  --region=us-central1 --project=rait-qa-agent --configuration=rait-dev
```

**Backend health check:**
```
https://rait-qa-backend-tqmp6uu44a-uc.a.run.app/health
```

---

## Cost (Phase 1 — Development)
~**£10–13/month** total. Runway: ~23 months on the $300 credit.

- Cloud Run CPU: ~$0 (free tier covers dev volume)
- Cloud Run GPU: ~$3/month (min=0, billed per 45s triage call)
- Cloud SQL: ~$9.36/month (pause it when not working to save $7)
- GCS: ~$0.30/month

---

*All code is on `main` at `github.com/LamaqRAIT/rait-qa-demo`. The rework commit is `ffa777e`.*
