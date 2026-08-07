#!/usr/bin/env bash
# One-time-ish setup + deploy for running the screener as a daily Cloud Run Job.
# Fill in the variables below, then run this script (or copy/paste sections as needed).
set -euo pipefail

PROJECT_ID="your-gcp-project-id"
REGION="us-central1"
REPO="special-sits-screener"           # Artifact Registry repo name
JOB_NAME="special-sits-screener"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${JOB_NAME}:latest"
SCHEDULER_JOB="special-sits-screener-daily"
SA_NAME="special-sits-screener-runner"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID"

# --- one-time setup -----------------------------------------------------------

gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com

gcloud artifacts repositories create "$REPO" \
  --repository-format=docker --location="$REGION" \
  --description="Special situations screener" || true

gcloud iam service-accounts create "$SA_NAME" \
  --display-name="Special sits screener runner" || true

# Store secrets (do NOT bake these into the image or .env in the container).
echo -n "your-app-password" | gcloud secrets create SMTP_PASSWORD --data-file=- || true
gcloud secrets add-iam-policy-binding SMTP_PASSWORD \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor"

# --- build + push --------------------------------------------------------------

gcloud builds submit --tag "$IMAGE" .

# --- create (or update) the Cloud Run Job --------------------------------------

gcloud run jobs deploy "$JOB_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$SA_EMAIL" \
  --max-retries=1 \
  --task-timeout=600 \
  --set-env-vars="SEC_USER_AGENT=SpecialSitsScreener jack@example.com,SMTP_USER=jackpargetter@gmail.com,EMAIL_FROM=jackpargetter@gmail.com,EMAIL_TO=jackpargetter@gmail.com" \
  --set-secrets="SMTP_PASSWORD=SMTP_PASSWORD:latest"

# --- schedule it daily ----------------------------------------------------------
# Cloud Scheduler needs its own SA with permission to invoke the job.

gcloud iam service-accounts create scheduler-invoker --display-name="Cloud Scheduler invoker" || true
INVOKER_SA="scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud run jobs add-iam-policy-binding "$JOB_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${INVOKER_SA}" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http "$SCHEDULER_JOB" \
  --location="$REGION" \
  --schedule="0 7 * * 1-5" \
  --time-zone="America/New_York" \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run" \
  --http-method=POST \
  --oauth-service-account-email="$INVOKER_SA"

# --- ad hoc runs / redeploys ------------------------------------------------
#   gcloud run jobs execute "$JOB_NAME" --region="$REGION"                      # run now
#   gcloud builds submit --tag "$IMAGE" . && gcloud run jobs update "$JOB_NAME" \
#     --image="$IMAGE" --region="$REGION"                                       # redeploy after code changes

# ================================================================================
# --- Dashboard: hosted, auto-refreshing webpage (separate from the daily email job) ---
# Reuses the same image ($IMAGE) and service account ($SA_EMAIL) as above -- just a
# different entrypoint (build_dashboard.py instead of main.py) on its own Cloud Run
# Job + Cloud Scheduler cron, writing to a public GCS bucket instead of sending email.
# Run the "one-time setup" and "build + push" sections above first.
# ================================================================================

DASHBOARD_JOB_NAME="special-sits-dashboard"
DASHBOARD_BUCKET="${PROJECT_ID}-specialsits-dashboard"   # bucket names must be globally unique
DASHBOARD_SCHEDULER_JOB="special-sits-dashboard-refresh"

# 1. Public GCS bucket to host dashboard.html
gcloud storage buckets create "gs://${DASHBOARD_BUCKET}" \
  --location="$REGION" --uniform-bucket-level-access || true

gcloud storage buckets add-iam-policy-binding "gs://${DASHBOARD_BUCKET}" \
  --member="allUsers" --role="roles/storage.objectViewer"

# 2. Let the job's service account write to it
gcloud storage buckets add-iam-policy-binding "gs://${DASHBOARD_BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/storage.objectAdmin"

# 3. Second Cloud Run Job, same image -- override the command instead of a second build
gcloud run jobs deploy "$DASHBOARD_JOB_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$SA_EMAIL" \
  --command="python" \
  --args="build_dashboard.py,/tmp/dashboard.html" \
  --max-retries=1 \
  --task-timeout=600 \
  --set-env-vars="SEC_USER_AGENT=SpecialSitsScreener jack@example.com,DASHBOARD_GCS_BUCKET=${DASHBOARD_BUCKET}"

# 4. Every 15 minutes (reuses the scheduler-invoker SA created for the email job above)
gcloud run jobs add-iam-policy-binding "$DASHBOARD_JOB_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${INVOKER_SA}" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http "$DASHBOARD_SCHEDULER_JOB" \
  --location="$REGION" \
  --schedule="*/15 * * * *" \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${DASHBOARD_JOB_NAME}:run" \
  --http-method=POST \
  --oauth-service-account-email="$INVOKER_SA"

# Live at: https://storage.googleapis.com/${DASHBOARD_BUCKET}/dashboard.html
# (the page itself also auto-reloads every 15 min via <meta refresh> -- leave a tab open)

# --- ad hoc runs / redeploys ------------------------------------------------
#   gcloud run jobs execute "$DASHBOARD_JOB_NAME" --region="$REGION"            # refresh now
#   gcloud builds submit --tag "$IMAGE" . && gcloud run jobs update "$DASHBOARD_JOB_NAME" \
#     --image="$IMAGE" --region="$REGION"                                       # redeploy after code changes
