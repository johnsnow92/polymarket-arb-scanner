#!/bin/bash
# One-time AWS infrastructure setup for the Polymarket Arb Scanner.
# Run this once to create all required AWS resources.
#
# Prerequisites:
#   - AWS CLI v2 installed and configured (aws configure)
#   - Docker installed
#
# Usage: bash infra/setup.sh

set -euo pipefail

REGION="us-east-1"
CLUSTER="arb-scanner"
SERVICE="arb-scanner-service"
REPO_NAME="polymarket-arb-scanner"
LOG_GROUP="/ecs/arb-scanner"
EFS_NAME="arb-scanner-data"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "AWS Account: $ACCOUNT_ID"
echo "Region:      $REGION"
echo ""

# -----------------------------------------------------------------------
# 1. ECR Repository
# -----------------------------------------------------------------------
echo "=== Creating ECR repository ==="
aws ecr describe-repositories --repository-names "$REPO_NAME" --region "$REGION" 2>/dev/null \
  || aws ecr create-repository --repository-name "$REPO_NAME" --region "$REGION"
ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME"
echo "ECR URI: $ECR_URI"
echo ""

# -----------------------------------------------------------------------
# 2. CloudWatch Log Group
# -----------------------------------------------------------------------
echo "=== Creating CloudWatch log group ==="
aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" --region "$REGION" \
  --query "logGroups[?logGroupName=='$LOG_GROUP'].logGroupName" --output text | grep -q "$LOG_GROUP" \
  || aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION"
# Retain logs for 30 days to control costs
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 30 --region "$REGION"
echo "Log group: $LOG_GROUP (30-day retention)"
echo ""

# -----------------------------------------------------------------------
# 3. Default VPC and Subnets
# -----------------------------------------------------------------------
echo "=== Discovering default VPC ==="
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query "Vpcs[0].VpcId" --output text --region "$REGION")

if [ "$VPC_ID" = "None" ] || [ -z "$VPC_ID" ]; then
  echo "ERROR: No default VPC found. Create one with: aws ec2 create-default-vpc"
  exit 1
fi
echo "VPC: $VPC_ID"

SUBNET_IDS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" \
  --query "Subnets[*].SubnetId" --output text --region "$REGION")
echo "Subnets: $SUBNET_IDS"
echo ""

# -----------------------------------------------------------------------
# 4. Security Group
# -----------------------------------------------------------------------
echo "=== Creating security group ==="
SG_ID=$(aws ec2 describe-security-groups \
  --filters Name=group-name,Values=arb-scanner-sg Name=vpc-id,Values="$VPC_ID" \
  --query "SecurityGroups[0].GroupId" --output text --region "$REGION" 2>/dev/null)

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group \
    --group-name arb-scanner-sg \
    --description "Arb scanner Fargate security group" \
    --vpc-id "$VPC_ID" \
    --query "GroupId" --output text --region "$REGION")

  # Allow outbound internet (API calls)
  # Inbound: allow 8080 for dashboard (restrict to your IP in production)
  aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --protocol tcp --port 8080 --cidr 0.0.0.0/0 \
    --region "$REGION" 2>/dev/null || true

  # Allow inbound NFS (port 2049) from within the security group (EFS access)
  aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --protocol tcp --port 2049 --source-group "$SG_ID" \
    --region "$REGION" 2>/dev/null || true
fi
echo "Security group: $SG_ID"
echo ""

# -----------------------------------------------------------------------
# 5. EFS File System
# -----------------------------------------------------------------------
echo "=== Creating EFS file system ==="
EFS_ID=$(aws efs describe-file-systems \
  --query "FileSystems[?Name=='$EFS_NAME'].FileSystemId | [0]" --output text --region "$REGION")

if [ "$EFS_ID" = "None" ] || [ -z "$EFS_ID" ]; then
  EFS_ID=$(aws efs create-file-system \
    --performance-mode generalPurpose \
    --throughput-mode bursting \
    --tags Key=Name,Value="$EFS_NAME" \
    --query "FileSystemId" --output text --region "$REGION")

  echo "Waiting for EFS to become available..."
  aws efs describe-file-systems --file-system-id "$EFS_ID" --region "$REGION" \
    --query "FileSystems[0].LifeCycleState" --output text
  sleep 10
fi
echo "EFS ID: $EFS_ID"

# Create mount targets in each subnet
for SUBNET in $SUBNET_IDS; do
  aws efs create-mount-target \
    --file-system-id "$EFS_ID" \
    --subnet-id "$SUBNET" \
    --security-groups "$SG_ID" \
    --region "$REGION" 2>/dev/null || true
done
echo "EFS mount targets created"
echo ""

# -----------------------------------------------------------------------
# 6. IAM Roles
# -----------------------------------------------------------------------
echo "=== Creating IAM roles ==="

TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

# ECS Task Execution Role (pulls images, writes logs, reads secrets)
aws iam get-role --role-name ecsTaskExecutionRole 2>/dev/null \
  || aws iam create-role \
    --role-name ecsTaskExecutionRole \
    --assume-role-policy-document "$TRUST_POLICY"

aws iam attach-role-policy --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy || true

# Allow reading secrets from Secrets Manager
SECRETS_POLICY="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"secretsmanager:GetSecretValue\"],\"Resource\":\"arn:aws:secretsmanager:$REGION:$ACCOUNT_ID:secret:arb-scanner/*\"}]}"

aws iam put-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-name ArbScannerSecretsAccess \
  --policy-document "$SECRETS_POLICY" 2>/dev/null || true

# ECS Task Role (the container's own permissions — EFS access)
aws iam get-role --role-name ecsTaskRole 2>/dev/null \
  || aws iam create-role \
    --role-name ecsTaskRole \
    --assume-role-policy-document "$TRUST_POLICY"

EFS_POLICY="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"elasticfilesystem:ClientMount\",\"elasticfilesystem:ClientWrite\"],\"Resource\":\"arn:aws:elasticfilesystem:$REGION:$ACCOUNT_ID:file-system/$EFS_ID\"}]}"

aws iam put-role-policy \
  --role-name ecsTaskRole \
  --policy-name ArbScannerEFSAccess \
  --policy-document "$EFS_POLICY" 2>/dev/null || true

echo "IAM roles configured"
echo ""

# -----------------------------------------------------------------------
# 7. ECS Cluster
# -----------------------------------------------------------------------
echo "=== Creating ECS cluster ==="
aws ecs describe-clusters --clusters "$CLUSTER" --region "$REGION" \
  --query "clusters[?status=='ACTIVE'].clusterName" --output text | grep -q "$CLUSTER" \
  || aws ecs create-cluster --cluster-name "$CLUSTER" --region "$REGION"
echo "Cluster: $CLUSTER"
echo ""

# -----------------------------------------------------------------------
# 8. Register Task Definition
# -----------------------------------------------------------------------
echo "=== Registering task definition ==="

# Substitute placeholders in task definition and register
TASK_DEF=$(sed -e "s/ACCOUNT_ID/$ACCOUNT_ID/g" -e "s/EFS_FILE_SYSTEM_ID/$EFS_ID/g" infra/task-definition.json)

aws ecs register-task-definition \
  --cli-input-json "$TASK_DEF" \
  --region "$REGION"
echo "Task definition registered"
echo ""

# -----------------------------------------------------------------------
# 9. Create ECS Service
# -----------------------------------------------------------------------
echo "=== Creating ECS service ==="

# Pick the first two subnets for the service
SUBNET_ARRAY=$(echo "$SUBNET_IDS" | tr '\t' ',' | cut -d',' -f1,2)

aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --region "$REGION" \
  --query "services[?status=='ACTIVE'].serviceName" --output text | grep -q "$SERVICE" \
  || aws ecs create-service \
    --cluster "$CLUSTER" \
    --service-name "$SERVICE" \
    --task-definition arb-scanner \
    --desired-count 1 \
    --launch-type FARGATE \
    --platform-version LATEST \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_ARRAY],securityGroups=[$SG_ID],assignPublicIp=ENABLED}" \
    --region "$REGION"
echo "Service: $SERVICE"
echo ""

# -----------------------------------------------------------------------
# 10. Summary
# -----------------------------------------------------------------------
echo "============================================="
echo "  Infrastructure setup complete!"
echo "============================================="
echo ""
echo "Resources created:"
echo "  ECR:       $ECR_URI"
echo "  EFS:       $EFS_ID"
echo "  Cluster:   $CLUSTER"
echo "  Service:   $SERVICE"
echo "  Log Group: $LOG_GROUP"
echo "  SG:        $SG_ID"
echo ""
echo "Next steps:"
echo "  1. Store secrets in AWS Secrets Manager:"
echo "     aws secretsmanager create-secret --name arb-scanner/kalshi-api-key-id --secret-string 'YOUR_KEY'"
echo "     aws secretsmanager create-secret --name arb-scanner/kalshi-private-key --secret-string 'BASE64_KEY'"
echo "     aws secretsmanager create-secret --name arb-scanner/polymarket-private-key --secret-string 'YOUR_KEY'"
echo ""
echo "  2. Build and deploy:"
echo "     bash infra/deploy.sh"
echo ""
echo "  3. Monitor:"
echo "     aws logs tail $LOG_GROUP --follow --region $REGION"
echo "     aws ecs describe-services --cluster $CLUSTER --services $SERVICE --region $REGION"
