# Quick Start Guide

Run Jarvis Registry locally with Docker Compose and prebuilt images from GitHub Container Registry (GHCR).

## What You'll Accomplish

By the end of this guide, you'll have:

- ✅ Jarvis Registry running locally
- ✅ Authentication configured with Entra ID
- ✅ A registry ready to connect MCP servers and agents

## Prerequisites

- **Identity Provider**: An Entra ID application registration and permission to configure its callback URL and ID token claims. Follow the [Entra ID setup](entra-id-setup.md) and [scope-group setup](runbooks/entra-scope-groups.md) to prepare the application, four security groups, and an account in at least one of those groups.
- **Container Runtime**: Docker running, with Docker Compose v2 or later available through `docker compose`.
- **Command Line Tools**: Git and OpenSSL available in your terminal.
- **Network Access**: Access to GHCR, the dependency image registries, and Microsoft Entra ID.

The `full` Compose profile includes MongoDB, Redis, Weaviate, and the observability services. Allow time and disk space for the initial image downloads. Startup time depends on your network, image cache, and machine.

## Step 1: Clone and Configure

```bash
# Clone the repository
git clone https://github.com/ascending-llc/jarvis-registry.git
cd jarvis-registry

# Verify you're in the right directory
ls -la
# Should see: docker-compose.yml, .env.example, README.md, etc.

# Copy and edit environment configuration
cp .env.example .env
```

## Step 2: Configure Authentication Provider

**Edit `.env` with your values:**

```bash
# Set authentication provider to Microsoft Entra ID
AUTH_PROVIDER=entra

ENTRA_TENANT_ID=your_tenant_id_here

# Azure AD Application (Client) ID
# Get this from Azure Portal > App Registrations > Your App > Overview > Application (client) ID
# Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ENTRA_CLIENT_ID=your_application_client_id_here

# Azure AD Client Secret
# Get this from Azure Portal > App Registrations > Your App > Certificates & secrets > Client secrets
# IMPORTANT: Copy the SECRET VALUE, not the Secret ID
# Format: xxx~xxxxxxxxxxxxxxxxxxxxxxxxxxxx
ENTRA_CLIENT_SECRET=your_client_secret_value_here

# Object IDs of the four Entra security groups in your tenant
JARVIS_REGISTRY_ADMIN_GROUP_OBJECT_ID=your_admin_group_object_id
JARVIS_REGISTRY_POWER_USER_GROUP_OBJECT_ID=your_power_user_group_object_id
JARVIS_REGISTRY_USER_GROUP_OBJECT_ID=your_user_group_object_id
JARVIS_REGISTRY_READ_ONLY_GROUP_OBJECT_ID=your_read_only_group_object_id
```

All four group IDs must be valid, distinct UUIDs. Follow the [scope-group setup](runbooks/entra-scope-groups.md) to obtain them and assign group membership.

In the Entra app registration, open **Token configuration → Add optional claim → ID** and add `email` and `xms_edov`. First-time sign-in requires a nonempty `email` claim and `xms_edov` set to the boolean `true`. Ensure the account has an email address that Entra can verify. See [required identity claims](entra-id-setup.md#required-identity-claims-and-shared-application).

Set the local service addresses in `.env`:

```dotenv
REGISTRY_URL=http://localhost:7860
REGISTRY_CLIENT_URL=http://localhost
AUTH_SERVER_URL=http://auth-server:8888
AUTH_SERVER_EXTERNAL_URL=http://localhost:8888
AUTH_SERVER_API_PREFIX=
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_DOMAIN=
```

In the Entra application's **Authentication** settings, add a **Web** redirect URI of `http://localhost:8888/oauth2/callback/entra`. This matches the empty `AUTH_SERVER_API_PREFIX` above. If you configure a prefix such as `/auth`, include it in the redirect URI as well.

## Step 3: Generate Secret Keys

Use the interactive **[Generate Secrets](https://jarvisregistry.com/generate-secrets/)** page to create your `SECRET_KEY`, `CREDS_KEY`, `JWT_PRIVATE_KEY`, and `JWT_PUBLIC_KEY` in the browser. Copy the `.env` output into your `.env` file, replacing the existing placeholder assignments. Preserve the quotes and line breaks around the PEM keys.

Generate a separate secret for Registry's OAuth client:

```bash
openssl rand -hex 32
```

Set `REGISTRY_CLIENT_SECRET` in `.env` to the generated value. Compose passes this shared configuration to Registry and the auth server.

## Step 4: Launch Services

The application images are published under `ghcr.io/ascending-llc/jarvis/` and currently target `linux/amd64`.

On an ARM64 machine, including Apple Silicon, use a container runtime with AMD64 emulation support and create `docker-compose.override.yml` in the repository root with the following content. If the file already exists, merge these entries into its `services` mapping.

```yaml
services:
  registry:
    platform: linux/amd64
  registry-migrations:
    platform: linux/amd64
  registry-frontend:
    platform: linux/amd64
  auth-server:
    platform: linux/amd64
  workflow-worker:
    platform: linux/amd64
  skill-scan-worker:
    platform: linux/amd64
```

Compose automatically loads this override file. The repository's `docker-compose.override.yml.example` is intended for building services from source.

```bash
# Validate configuration without printing credentials
docker compose --profile full config --quiet

# Download the application and dependency images
docker compose --profile full pull

# Start the services from the downloaded images
docker compose --profile full up -d --no-build
```

If a published port is already in use, follow [Port Already in Use](#port-already-in-use) before starting the services.

## Step 5: Verify Installation

```bash
# Include completed initialization tasks
docker compose --profile full ps -a
```

`registry-migrations` should finish with **Exited (0)**. The application services wait for this task to complete. MongoDB, Redis, and Weaviate should report **healthy**, and the remaining services should be running.

| Service | Default host ports |
| --- | --- |
| `registry-frontend` | 80 |
| `registry` | 7860 |
| `auth-server` | 8888 |
| `workflow-worker`, `skill-scan-worker` | None |
| `registry-mongodb` | 27017 |
| `registry-redis` | 6379 |
| `weaviate` | 8099, 50051 |
| `otel-collector` | 4317, 4318, 8889 |
| `tempo` | 3200 |
| `prometheus` | 9090 |
| `grafana` | 3000 |

Once the services are ready, check both application health endpoints:

```bash
curl --fail http://localhost:7860/health
curl --fail http://localhost:8888/health
```

Open [http://localhost/](http://localhost/) in your browser and sign in with your Entra ID account.

## 🎉 Success! What's Next?

With the services running and sign-in configured, you can connect tools and configure additional features.

The default embedding provider is AWS Bedrock. Configure an AWS identity available inside the containers before using Bedrock-dependent vector search, reranking, or LLM workflows. The model-provider settings are documented in [`.env.example`](https://github.com/ascending-llc/jarvis-registry/blob/main/.env.example).

### Immediate Next Steps

- 🔍 **Explore the Web Interface** - Browse available MCP servers and tools
- 🤖 **Try AI Assistant Integration** - Use tools through VS Code or your preferred AI assistant
- 🛠️ **Add Your Own MCP Servers** - Register custom tools for your team

### Enterprise Features

- 👥 **[Fine-Grained Access Control](design/scopes.md)** - Team-based permissions

## Troubleshooting Quick Fixes

### Can't Access Web Interface

```bash
# Check if registry is running
curl --fail http://localhost:7860/health

# Check application and initialization logs
docker compose --profile full logs --tail=100 registry auth-server registry-migrations
```

### Port Already in Use

If a port is already in use, change the host port in the affected service's `ports` mapping in `docker-compose.yml` to an unused port, keeping the container port unchanged. Then run `docker compose --profile full up -d --no-build` again.

If you change a browser-facing port, update the corresponding URLs in `.env` and use the new port when accessing the service. Changing the auth server's external port also requires updating the Entra Web redirect URI.

### Entra Sign-In Fails

- For a redirect URI mismatch, check that the Entra Web redirect URI matches the auth server's external URL, API prefix, and `/oauth2/callback/entra` path.
- For an email verification error, check the ID token's `email` and `xms_edov` claims and the account's email configuration. See [required identity claims](entra-id-setup.md#required-identity-claims-and-shared-application).
- For missing permissions, check the four group Object IDs and the account's membership using the [scope-group setup](runbooks/entra-scope-groups.md).

### Warnings About Unset Integration Variables

Variables such as `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `SRE_GATEWAY_AUTH_TOKEN`, `ATLASSIAN_AUTH_TOKEN`, `ASOR_ACCESS_TOKEN`, and `ASOR_CLIENT_CREDENTIALS` belong to optional integrations. Configure them when enabling those integrations. The Entra credentials, group bindings, and application keys in this guide are required for this setup.

## Getting Help

- 🐛 **[GitHub Issues](https://github.com/ascending-llc/jarvis-registry/issues)** - Bug reports and feature requests
