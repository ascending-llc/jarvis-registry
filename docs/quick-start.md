# Quick Start Guide

Run Jarvis Registry locally with Docker Compose and prebuilt images from GitHub Container Registry (GHCR).

## What You'll Accomplish

By the end of this guide, you'll have:

- ✅ Jarvis Registry running locally
- ✅ Authentication configured with Entra ID, Google Workspace, or both
- ✅ A registry ready to connect MCP servers and agents

## Prerequisites

- **Identity Provider**: An Entra ID or Google Workspace organization, with permission to configure an OAuth application and access groups. Follow the [Entra ID setup](entra-id-setup.md) and [scope-group setup](runbooks/entra-scope-groups.md), or [Google SSO onboarding](runbooks/google-sso-onboarding.md), for your chosen provider.
- **Container Runtime**: Docker running, with Docker Compose v2 or later available through `docker compose`.
- **Command Line Tools**: Git and OpenSSL available in your terminal.
- **Network Access**: Access to GHCR, the dependency image registries, and your identity provider's authentication and group APIs.

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

Set the local service addresses in `.env`. These settings are shared by both providers:

```dotenv
REGISTRY_URL=http://localhost:7860
REGISTRY_CLIENT_URL=http://localhost
AUTH_SERVER_URL=http://auth-server:8888
AUTH_SERVER_EXTERNAL_URL=http://localhost:8888
AUTH_SERVER_API_PREFIX=
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_DOMAIN=
```

The callback URLs below use an empty `AUTH_SERVER_API_PREFIX`. If you configure a prefix such as `/auth`, include it before `/oauth2/callback/` in the registered callback URLs.

Choose one provider option below, or configure both using the instructions after the options.

### Option A: Microsoft Entra ID

Edit `.env` with your values:

```dotenv
AUTH_PROVIDER=entra
ENTRA_ENABLED=true
GOOGLE_ENABLED=false

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

In the Entra application's **Authentication** settings, add a **Web** redirect URI of `http://localhost:8888/oauth2/callback/entra`.

### Option B: Google Workspace

Follow [Google SSO onboarding](runbooks/google-sso-onboarding.md) to create a **Web application** OAuth client, enable the Cloud Identity API, and prepare a service account with permission to read Workspace group memberships. Register `http://localhost:8888/oauth2/callback/google` as an **Authorized redirect URI** on the same OAuth client used for `GOOGLE_CLIENT_ID` below.

Edit `.env` with your values:

```dotenv
AUTH_PROVIDER=google
GOOGLE_ENABLED=true
ENTRA_ENABLED=false

GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_ALLOWED_HD=yourcompany.com
GOOGLE_CUSTOMER_ID=Cxxxxxxxx
GOOGLE_SERVICE_ACCOUNT_KEY_JSON='<full service account key JSON on one line>'
GOOGLE_GROUP_SYNC_ENABLED=true
```

Replace the service-account placeholder with the complete JSON key, retaining the single quotes and the JSON's escaped `\n` characters. `GOOGLE_CUSTOMER_ID` is your Workspace customer ID, available in **Admin Console → Account → Account settings**; it keeps group lookups within your organization.

Use Workspace groups named `jarvis-registry-admin`, `jarvis-registry-power-user`, `jarvis-registry-user`, or `jarvis-registry-read-only`, with group email addresses on exactly the `GOOGLE_ALLOWED_HD` domain. Add your account to at least one appropriate group. A verified Google email alone is insufficient: browser sign-in also requires permissions from these groups. For Google-only deployments, leave the four `JARVIS_REGISTRY_*_GROUP_OBJECT_ID` values empty.

### Enable Both Providers

Configure the credentials, groups, and callback URLs from both options, then set:

```dotenv
ENTRA_ENABLED=true
GOOGLE_ENABLED=true
AUTH_PROVIDER=entra
```

`ENTRA_ENABLED` and `GOOGLE_ENABLED` control the available login buttons. `AUTH_PROVIDER` selects the single provider advertised to MCP/OAuth clients; use `entra` or `google` according to your preferred default. Keep the four Entra group Object IDs configured whenever Entra is enabled, even if `AUTH_PROVIDER=google`.

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

After changing `.env`, run the same `up` command again so Compose recreates services with the updated environment. `docker compose restart` does not apply environment changes.

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

Check the enabled login providers:

```bash
curl --fail http://localhost:8888/oauth2/providers
```

The response should list `entra`, `google`, or both, matching your settings. Include your auth API prefix in this URL if you configured one.

Open [http://localhost/](http://localhost/) in your browser and sign in with an account in one of the configured access groups. When both providers are enabled, verify each login button with an account from that provider.

## 🎉 Success! What's Next?

With the services running and sign-in configured, you can connect tools and configure additional features.

To enable semantic vector search, configure an embedding model so the Registry can match natural-language inquiries to registered MCP servers and A2A agents. You can register and select a model in the Registry or configure the embedding provider through environment settings. If you use the default AWS Bedrock provider, make AWS credentials available inside the containers. See the [Model Source Management API reference](design/model-source-api.md) for model selection and [Embeddings Configuration](embeddings.md) for provider settings.

### Choose Your Next Path

After signing in, continue with the capability that best fits your use case:

- **Connect AI coding assistants and chat clients** - First register the capabilities you want to expose: [MCP servers](features/mcp-gateway-registry.md) for tools and services, or [A2A agents](features/a2a-gateway-registry.md) for agent capabilities. Then connect a compatible client to the shared endpoint using the [Registry Endpoint guide](features/registry-endpoint.md).
- **Manage private AI skills** - Create or import skills, govern access, and distribute approved skills to developer tools with the [Skill Gateway](features/skill-gateway.md).
- **Register AI agents** - Catalog A2A agents, manage access, and make permitted agents available through the gateway with the [A2A Agent Gateway & Registry](features/a2a-gateway-registry.md).
- **Build AI workflows** - Combine agents, MCP tools, and skills into multi-step processes with [Workflow Orchestration](features/workflow-orchestration.md).
- **Configure AI models** - Manage model providers and select the default workflow/chat and embedding models in the [Model Source Management API reference](design/model-source-api.md).

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

If a port is already in use, remap the host port in `docker-compose.override.yml` instead of editing the tracked `docker-compose.yml`. Keep the container port unchanged and replace the service's whole `ports` list with `!override`, which requires Docker Compose 2.24.4 or later. Without it, Compose appends your mapping to the original one and the conflicting port is still published. For example, to move MongoDB to host port 27018, merge this into the file's `services` mapping:

```yaml
services:
  registry-mongodb:
    ports: !override
      - "27018:27017"
```

List every mapping you want to keep for that service, since `!override` replaces the whole list. Then run `docker compose --profile full up -d --no-build` again.

If you change a browser-facing port, update the corresponding URLs in `.env` and use the new port when accessing the service. Changing the auth server's external port also requires updating each enabled identity provider's registered redirect URI.

### Entra Sign-In Fails

- For a redirect URI mismatch, check that the Entra Web redirect URI matches the auth server's external URL, API prefix, and `/oauth2/callback/entra` path.
- For an email verification error, check the ID token's `email` and `xms_edov` claims and the account's email configuration. See [required identity claims](entra-id-setup.md#required-identity-claims-and-shared-application).
- For missing permissions, check the four group Object IDs and the account's membership using the [scope-group setup](runbooks/entra-scope-groups.md).

### Google Sign-In Fails

- If the Google button is missing, set `GOOGLE_ENABLED=true` and apply the `.env` change with the `up` command in Step 4.
- For a redirect URI mismatch, check the Google OAuth client's Authorized redirect URI against the auth server's external URL, API prefix, and `/oauth2/callback/google` path.
- For an email or domain error, use a verified Workspace account on the `GOOGLE_ALLOWED_HD` domain.
- For missing permissions, check the Cloud Identity API, service account key and group-read permissions, Workspace customer ID, and the account's membership in a correctly named group. See [Google SSO onboarding](runbooks/google-sso-onboarding.md).

## Getting Help

- 🐛 **[GitHub Issues](https://github.com/ascending-llc/jarvis-registry/issues)** - Bug reports and feature requests
