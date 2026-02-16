#!/bin/bash
# Build, push, and deploy the Polymarket Arb Scanner to AWS ECS Fargate.
#
# Prerequisites:
#   - AWS CLI configured
#   - Docker running
#   - Infrastructure created via: bash infra/setup.sh
#
# Usage: bash infra/deploy.sh

set -euo pipefail

REGION="us-east-1"
CLUSTER="arb-scanner"
SERVICE="arb-scanner-service"
REPO_NAME="polymarket-arb-scanner"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME"

echo "=== Deploying $REPO_NAME ==="
echo "Account: $ACCOUNT_ID"
echo "ECR:     $ECR_URI"
echo ""

# -----------------------------------------------------------------------
# 1. Login to ECR
# -----------------------------------------------------------------------
echo "--- Logging in to ECR ---"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
echo ""

# -----------------------------------------------------------------------
# 2. Build Docker image
# -----------------------------------------------------------------------
echo "--- Building Docker image ---"
docker build -t "$REPO_NAME" .
echo ""

# -----------------------------------------------------------------------
# 3. Tag and push to ECR
# -----------------------------------------------------------------------
echo "--- Pushing to ECR ---"
docker tag "$REPO_NAME:latest" "$ECR_URI:latest"
docker push "$ECR_URI:latest"

# Also tag with timestamp for rollback capability
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
docker tag "$REPO_NAME:latest" "$ECR_URI:$TIMESTAMP"
docker push "$ECR_URI:$TIMESTAMP"
echo "Pushed: $ECR_URI:latest and $ECR_URI:$TIMESTAMP"
echo ""

# -----------------------------------------------------------------------
# 4. Force new deployment
# -----------------------------------------------------------------------
echo "--- Updating ECS service ---"
aws ecs update-service \
  --cluster "$CLUSTER" \
  --service "$SERVICE" \
  --force-new-deployment \
  --region "$REGION" \
  --query "service.deployments[0].{status:status,desired:desiredCount,running:runningCount}" \
  --output table
echo ""

# -----------------------------------------------------------------------
# 5. Wait for stability
# -----------------------------------------------------------------------
echo "--- Waiting for service to stabilize (this may take a few minutes) ---"
aws ecs wait services-stable \
  --cluster "$CLUSTER" \
  --services "$SERVICE" \
  --region "$REGION" \
  && echo "Service is stable and running!" \
  || echo "WARNING: Service did not stabilize within the timeout. Check CloudWatch logs."

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Monitor:"
echo "  aws logs tail /ecs/arb-scanner --follow --region $REGION"
echo "  aws ecs describe-services --cluster $CLUSTER --services $SERVICE --region $REGION --query 'services[0].{status:status,running:runningCount,desired:desiredCount}'"
