#!/usr/bin/env bash
# Deploy Turnover to Amazon ECS Express Mode (Fargate behind an HTTPS load balancer).
#
# Creates, or reuses on re-run:
#   ECR repository              container image
#   Secrets Manager secrets     Ring client secret + HMAC key (never in env vars or git)
#   SNS topic                   host alerts by email/SMS (subscribe yourself with ALERT_EMAIL)
#   IAM roles                   execution (pull image, read the two secrets),
#                               infrastructure (Express Mode), task (Bedrock + SNS only)
#   ECS Express service         public HTTPS URL: https://<name>.ecs.<region>.on.aws/
#
# Usage:
#   export AWS_REGION=us-east-1
#   export RING_HMAC_KEY=...            # from the Ring Developer Portal (optional for the demo)
#   export RING_CLIENT_ID=... RING_CLIENT_SECRET=...   # optional
#   export ALERT_EMAIL=you@example.com  # optional, confirms an SNS email subscription
#   export DASHBOARD_PASSWORD=...       # optional, puts the dashboard behind a password
#   ./scripts/deploy_aws.sh
#
# Requires: AWS CLI v2 (recent enough for `aws ecs create-express-gateway-service`), Docker.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
NAME="${SERVICE_NAME:-turnover}"
DEMO="${TURNOVER_DEMO:-1}"
MODEL="${BEDROCK_MODEL_ID:-us.amazon.nova-lite-v1:0}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
IMAGE="${REGISTRY}/${NAME}:${IMAGE_TAG}"
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/6 Container image -> ${IMAGE}"
aws ecr describe-repositories --repository-names "$NAME" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$NAME" --region "$REGION" --image-scanning-configuration scanOnPush=true >/dev/null
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
docker build --platform linux/amd64 -t "$IMAGE" .
docker push "$IMAGE" >/dev/null

say "2/6 Secrets"
put_secret() {  # name value -> prints ARN
  local name="$1" value="$2"
  if aws secretsmanager describe-secret --secret-id "$name" --region "$REGION" >/dev/null 2>&1; then
    [ -n "$value" ] && aws secretsmanager put-secret-value --secret-id "$name" --secret-string "$value" --region "$REGION" >/dev/null
  else
    aws secretsmanager create-secret --name "$name" --secret-string "${value:-unset}" --region "$REGION" >/dev/null
  fi
  aws secretsmanager describe-secret --secret-id "$name" --region "$REGION" --query ARN --output text
}
HMAC_ARN="$(put_secret "${NAME}/ring-hmac-key" "${RING_HMAC_KEY:-}")"
CLIENT_SECRET_ARN="$(put_secret "${NAME}/ring-client-secret" "${RING_CLIENT_SECRET:-}")"
PASSWORD_ARN="$(put_secret "${NAME}/dashboard-password" "${DASHBOARD_PASSWORD:-}")"

say "3/6 SNS topic for host alerts"
TOPIC_ARN="$(aws sns create-topic --name "${NAME}-alerts" --region "$REGION" --query TopicArn --output text)"
if [ -n "${ALERT_EMAIL:-}" ]; then
  aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol email --notification-endpoint "$ALERT_EMAIL" --region "$REGION" >/dev/null
  echo "Check ${ALERT_EMAIL} and confirm the SNS subscription."
fi

say "4/6 IAM roles"
ensure_role() {  # name service
  aws iam get-role --role-name "$1" >/dev/null 2>&1 || aws iam create-role --role-name "$1" \
    --assume-role-policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"$2\"},\"Action\":\"sts:AssumeRole\"}]}" >/dev/null
  aws iam get-role --role-name "$1" --query Role.Arn --output text
}
EXEC_ROLE="$(ensure_role "${NAME}-execution" ecs-tasks.amazonaws.com)"
aws iam attach-role-policy --role-name "${NAME}-execution" --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam put-role-policy --role-name "${NAME}-execution" --policy-name read-turnover-secrets --policy-document "{
  \"Version\":\"2012-10-17\",
  \"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":[\"${HMAC_ARN}\",\"${CLIENT_SECRET_ARN}\",\"${PASSWORD_ARN}\"]}]}"
INFRA_ROLE="$(ensure_role "${NAME}-infrastructure" ecs.amazonaws.com)"
aws iam attach-role-policy --role-name "${NAME}-infrastructure" --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices
TASK_ROLE="$(ensure_role "${NAME}-task" ecs-tasks.amazonaws.com)"
aws iam put-role-policy --role-name "${NAME}-task" --policy-name bedrock-and-sns --policy-document "{
  \"Version\":\"2012-10-17\",
  \"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":[\"bedrock:InvokeModel\",\"bedrock:ApplyGuardrail\"],\"Resource\":\"*\"},
    {\"Effect\":\"Allow\",\"Action\":\"sns:Publish\",\"Resource\":\"${TOPIC_ARN}\"}]}"
sleep 10  # IAM is eventually consistent

say "5/6 ECS Express Mode service"
CONTAINER="$(cat <<JSON
{"image":"${IMAGE}","containerPort":8080,
 "environment":[
   {"name":"TURNOVER_DEMO","value":"${DEMO}"},
   {"name":"AWS_REGION","value":"${REGION}"},
   {"name":"BEDROCK_ENABLED","value":"${BEDROCK_ENABLED:-1}"},
   {"name":"BEDROCK_MODEL_ID","value":"${MODEL}"},
   {"name":"BEDROCK_GUARDRAIL_ID","value":"${BEDROCK_GUARDRAIL_ID:-}"},
   {"name":"SNS_TOPIC_ARN","value":"${TOPIC_ARN}"},
   {"name":"RING_CLIENT_ID","value":"${RING_CLIENT_ID:-}"}],
 "secrets":[
   {"name":"RING_HMAC_KEY","valueFrom":"${HMAC_ARN}"},
   {"name":"RING_CLIENT_SECRET","valueFrom":"${CLIENT_SECRET_ARN}"},
   {"name":"DASHBOARD_PASSWORD","valueFrom":"${PASSWORD_ARN}"}]}
JSON
)"
SERVICE_ARN="$(aws ecs list-services --cluster default --region "$REGION" --query "serviceArns[?contains(@, '/${NAME}')]|[0]" --output text 2>/dev/null || true)"
if [ -z "$SERVICE_ARN" ] || [ "$SERVICE_ARN" = "None" ]; then
  SERVICE_ARN="$(aws ecs create-express-gateway-service --region "$REGION" \
    --service-name "$NAME" \
    --execution-role-arn "$EXEC_ROLE" \
    --infrastructure-role-arn "$INFRA_ROLE" \
    --task-role-arn "$TASK_ROLE" \
    --primary-container "$CONTAINER" \
    --health-check-path /healthz \
    --cpu 1 --memory 2 \
    --scaling-target '{"minTaskCount":1,"maxTaskCount":1}' \
    --query service.serviceArn --output text)"
else
  aws ecs update-express-gateway-service --region "$REGION" --service-arn "$SERVICE_ARN" \
    --primary-container "$CONTAINER" --task-role-arn "$TASK_ROLE" --execution-role-arn "$EXEC_ROLE" >/dev/null
fi

say "6/6 Waiting for the service (a few minutes on first deploy)"
aws ecs monitor-express-gateway-service --region "$REGION" --service-arn "$SERVICE_ARN" || true
URL="$(aws ecs describe-express-gateway-service --region "$REGION" --service-arn "$SERVICE_ARN" \
  --query 'service.activeConfigurations[0].ingressPaths[0].endpoint' --output text)"
echo
echo "Turnover is live:  https://${URL#https://}"
echo "Ring Webhook URL:  https://${URL#https://}/webhooks/ring"
echo "Token Exchange:    https://${URL#https://}/ring/token-exchange"
echo "Account Link URL:  https://${URL#https://}/ring/link"
echo "App Homepage URL:  https://${URL#https://}/ring/setup"
