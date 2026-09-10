#!/usr/bin/env bash
# deploy.sh — build, push, and wire up Cloud Run Job + Cloud Scheduler
# Edit the variables below, then run: bash deploy.sh
set -euo pipefail

# ── CONFIGURE THESE ──────────────────────────────────────────────────────────
PROJECT_ID="autoremind-480200"   # gcloud projects list
REGION="us-central1"
TIMEZONE="America/Los_Angeles"     # IANA timezone for the 6 AM schedule
# ─────────────────────────────────────────────────────────────────────────────

IMAGE_NAME="simple-sync"
JOB_NAME="simple-sync"
SCHEDULER_JOB="simple-sync-daily"
SA_NAME="simple-sync-sa"

REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${IMAGE_NAME}"
IMAGE="${REPO}/${IMAGE_NAME}:latest"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# ── 1. APIs ───────────────────────────────────────────────────────────────────
echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  --project="${PROJECT_ID}"

# ── 2. Artifact Registry ──────────────────────────────────────────────────────
echo "==> Creating Artifact Registry repository (skips if exists)..."
gcloud artifacts repositories create "${IMAGE_NAME}" \
  --repository-format=docker \
  --location="${REGION}" \
  --project="${PROJECT_ID}" 2>/dev/null || true

# ── 3. Docker image ───────────────────────────────────────────────────────────
echo "==> Building and pushing Docker image..."
gcloud builds submit --tag "${IMAGE}" --project="${PROJECT_ID}" .

# ── 4. Secrets ────────────────────────────────────────────────────────────────
echo "==> Uploading secrets to Secret Manager..."
for secret_name in simple-sync-credentials simple-sync-config; do
  if ! gcloud secrets describe "${secret_name}" --project="${PROJECT_ID}" &>/dev/null; then
    gcloud secrets create "${secret_name}" \
      --replication-policy="automatic" \
      --project="${PROJECT_ID}"
  fi
done

gcloud secrets versions add simple-sync-credentials \
  --data-file="credentials.json" \
  --project="${PROJECT_ID}"

gcloud secrets versions add simple-sync-config \
  --data-file="config/custom_sync.json" \
  --project="${PROJECT_ID}"

# ── 5. Service account ────────────────────────────────────────────────────────
echo "==> Creating service account (skips if exists)..."
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" &>/dev/null; then
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Simple Sync Cloud Run SA" \
    --project="${PROJECT_ID}"
  echo "  waiting for service account to propagate..."
  sleep 15
fi

echo "==> Granting secret access to service account..."
for secret_name in simple-sync-credentials simple-sync-config; do
  gcloud secrets add-iam-policy-binding "${secret_name}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None \
    --project="${PROJECT_ID}"
done

echo "==> Granting Cloud Run invoker role to service account..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --condition=None

# ── 6. Cloud Run Job ──────────────────────────────────────────────────────────
echo "==> Creating/updating Cloud Run Job..."
JOB_ARGS=(
  --image="${IMAGE}"
  --region="${REGION}"
  --service-account="${SA_EMAIL}"
  --memory="512Mi"
  --cpu="1"
  --max-retries=1
  --task-timeout="60m"
  --set-secrets="/app/credentials.json=simple-sync-credentials:latest,/app/config/custom_sync.json=simple-sync-config:latest"
  --project="${PROJECT_ID}"
)
gcloud run jobs create "${JOB_NAME}" "${JOB_ARGS[@]}" 2>/dev/null || \
gcloud run jobs update "${JOB_NAME}" "${JOB_ARGS[@]}"

# ── 7. Cloud Scheduler ────────────────────────────────────────────────────────
echo "==> Creating/updating Cloud Scheduler job (6 AM ${TIMEZONE} daily)..."
SCHEDULER_ARGS=(
  --location="${REGION}"
  --schedule="0 6 * * *"
  --time-zone="${TIMEZONE}"
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"
  --oauth-service-account-email="${SA_EMAIL}"
  --project="${PROJECT_ID}"
)
gcloud scheduler jobs create http "${SCHEDULER_JOB}" "${SCHEDULER_ARGS[@]}" 2>/dev/null || \
gcloud scheduler jobs update http "${SCHEDULER_JOB}" "${SCHEDULER_ARGS[@]}"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "Done. The job will run daily at 6 AM ${TIMEZONE}."
echo ""
echo "  Manual run:   gcloud run jobs execute ${JOB_NAME} --region=${REGION} --project=${PROJECT_ID}"
echo "  Logs:         gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=${JOB_NAME}' --project=${PROJECT_ID} --limit=50 --format='value(textPayload)'"
