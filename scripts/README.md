# MCP Gateway Registry Scripts

This directory contains utility scripts for building, testing, and deploying MCP Gateway Registry services.

## Federation Job Admin

Use the federation job admin helper to inspect or repair federation sync state.

Recommended command:

```bash
uv run federation-job-admin --help
```

Supported actions:
- `show <federation_id>`: inspect federation sync state plus recent jobs
- `list-active`: list all currently active federation jobs (`pending` / `syncing`)
- `fail-active <federation_id>`: mark the latest active job as failed and move the federation to `syncStatus=failed`
- `set-sync-state <federation_id>`: manually override federation `syncStatus` and `syncMessage`
- `retry-vector-sync <federation_id>`: rebuild Weaviate/vector index from the current Mongo state for that federation

Examples:

```bash
uv run federation-job-admin show federation-demo-id
uv run federation-job-admin list-active --limit 10
uv run federation-job-admin fail-active federation-demo-id --reason "manual recovery"
uv run federation-job-admin set-sync-state federation-demo-id --status failed --message "manual recovery"
uv run federation-job-admin retry-vector-sync federation-demo-id
```

Notes:
- `retry-vector-sync` does not rerun federation discovery or create a new federation job.
- `retry-vector-sync` reads the current MongoDB state and re-syncs existing MCP/A2A resources into the vector database.
- Running `retry-vector-sync` multiple times should converge to the same final vector state.

## Keycloak Build & Push Script

### Overview

The `build-and-push-keycloak.sh` script automates the process of building a Keycloak Docker image and pushing it to AWS ECR (Elastic Container Registry).

### Quick Start

```bash
# Build and push with defaults (latest tag to us-west-2)
./scripts/build-and-push-keycloak.sh

# Build and push with custom tag
./scripts/build-and-push-keycloak.sh --image-tag v24.0.1

# Build only (don't push)
./scripts/build-and-push-keycloak.sh --no-push
```

### Using with Make

```bash
# Build Keycloak image locally
make build-keycloak

# Build and push to ECR
make build-and-push-keycloak

# Deploy to ECS (after push)
make deploy-keycloak

# Complete workflow: build, push, and deploy
make update-keycloak

# With custom parameters
make build-and-push-keycloak AWS_REGION=us-east-1 IMAGE_TAG=v24.0.1
```

### Options

- `--aws-region REGION` - AWS region (default: us-west-2)
- `--image-tag TAG` - Image tag (default: latest)
- `--aws-profile PROFILE` - AWS profile (default: default)
- `--dockerfile PATH` - Dockerfile path (default: docker/keycloak/Dockerfile)
- `--build-context PATH` - Build context (default: docker/keycloak)
- `--no-push` - Build only, don't push to ECR
- `--help` - Show help message

### Prerequisites

- Docker installed and running
- AWS CLI installed and configured
- AWS credentials with ECR access
- Permission to push to ECR repository `keycloak`

### Features

- Color-coded output for easy readability
- Step-by-step progress tracking
- Error handling with clear error messages
- ECR login automation
- Image verification after push
- Helpful commands for manual deployment

### Workflow Example

```bash
# Build and push image
./scripts/build-and-push-keycloak.sh --image-tag v24.0.1

# Deploy to ECS
aws ecs update-service \
  --cluster keycloak \
  --service keycloak \
  --force-new-deployment \
  --region us-west-2

# Monitor deployment
aws ecs describe-services \
  --cluster keycloak \
  --services keycloak \
  --region us-west-2 \
  --query 'services[0].[serviceName,status,runningCount,desiredCount]' \
  --output table
```

### Troubleshooting

#### "Failed to get AWS account ID"
- Check AWS credentials: `aws sts get-caller-identity`
- Verify AWS profile: `aws configure list --profile <profile-name>`

#### "Failed to login to ECR"
- Verify ECR permissions in IAM
- Check if repository exists: `aws ecr describe-repositories --repository-names keycloak`

#### "Failed to build Docker image"
- Check Docker is running: `docker ps`
- Verify Dockerfile exists: `ls -la docker/keycloak/Dockerfile`

### Further Reading

- [AWS ECR Documentation](https://docs.aws.amazon.com/ecr/)
- [Keycloak Docker Image](https://hub.docker.com/r/keycloak/keycloak)
- [ECS Service Updates](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/update-service.html)
