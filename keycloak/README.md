# Keycloak Setup for MCP Gateway

## Quick Start - Set Up Keycloak in 4 Steps

### Prerequisites
- Docker and Docker Compose installed
- Port 8080 available (Keycloak) and 5432 (PostgreSQL)

### Step 1: Set Required Passwords
```bash
# MANDATORY - Set these before starting containers
export KEYCLOAK_ADMIN_PASSWORD="your-secure-admin-password"
export KEYCLOAK_DB_PASSWORD="your-secure-database-password"
```

### Step 2: Start Keycloak Services
```bash
# Start PostgreSQL and Keycloak containers
docker compose up -d keycloak-db  keycloak

# Wait for Keycloak to be ready (takes ~2 minutes)
echo "Waiting for Keycloak to start..."
sleep 120

# Verify Keycloak is running
curl -f http://localhost:8080/health/ready || echo "Keycloak not ready yet"
```

### Step 3: Initialize Keycloak
```bash
# This creates the realm, groups, and M2M client
./keycloak/setup/init-keycloak.sh
```

### Step 4: Create Service Accounts for Your Agents

**Option A: Production Setup (Individual Agents)**
```bash
# Create a service account for each AI agent
./keycloak/setup/setup-agent-service-account.sh \
  --agent-id sre-agent \
  --group registry-admin
```

**Option B: Development Setup (Shared Account)**
```bash
# Create one shared service account for all agents
./keycloak/setup/setup-m2m-service-account.sh
```

**That's it!** Keycloak is now configured for the MCP Gateway.

---

## What Each Script Does

### Available Scripts

| Script | Purpose | When to Use |
|--------|---------|------------|
| `init-keycloak.sh` | Creates realm, groups, and M2M client | **Always run first** during initial setup |
| `setup-agent-service-account.sh` | Creates individual service account for one AI agent | When adding a new AI agent (production) |
| `setup-m2m-service-account.sh` | Creates shared service account | For development/testing only |
| `clean-keycloak.sh` | Removes all Keycloak configuration | For complete reset (use with caution) |

### Script Details

#### init-keycloak.sh
**What it does:**
- Creates the `mcp-gateway` realm
- Creates four groups: `registry-admin`, `registry-power-user`, `registry-user`, `registry-read-only`
- Creates the M2M client (`mcp-gateway-m2m`)
- Configures group mappers for JWT tokens
- Sets up proper client scopes

**Required Environment Variables:**
- `KEYCLOAK_ADMIN_PASSWORD` - Admin password for Keycloak
- `KEYCLOAK_DB_PASSWORD` - Database password

**Usage:**
```bash
export KEYCLOAK_ADMIN_PASSWORD="secure-password"
export KEYCLOAK_DB_PASSWORD="secure-db-password"
./keycloak/setup/init-keycloak.sh
```

#### setup-agent-service-account.sh
**What it does:**
- Creates an individual service account for a specific AI agent
- Assigns the account to one of the role groups
- Enables individual audit trails per agent

**Required Environment Variables:**
- `KEYCLOAK_ADMIN_PASSWORD` - Admin password for Keycloak

**Usage:**
```bash
# For an agent with full access
./keycloak/setup/setup-agent-service-account.sh \
  --agent-id my-agent \
  --group registry-admin

# For an agent with limited access
./keycloak/setup/setup-agent-service-account.sh \
  --agent-id my-limited-agent \
  --group registry-read-only
```

**Options:**
- `--agent-id` - Unique identifier for the agent (required)
- `--group` - One of `registry-admin`, `registry-power-user`, `registry-user`, `registry-read-only` (required)

#### setup-m2m-service-account.sh
**What it does:**
- Creates a single shared service account
- Used for development/testing when you don't need individual agent tracking
- Assigns to registry-admin group by default

**Required Environment Variables:**
- `KEYCLOAK_ADMIN_PASSWORD` - Admin password for Keycloak

**Usage:**
```bash
./keycloak/setup/setup-m2m-service-account.sh
```

---

## Common Tasks

### Adding a New AI Agent
```bash
# 1. Create the service account
./keycloak/setup/setup-agent-service-account.sh \
  --agent-id new-agent \
  --group registry-read-only

# 2. Generate token (using Python script)
cd credentials-provider
python token_refresher.py --agent-id new-agent

# 3. Test the setup
./test-keycloak-mcp.sh --agent-id new-agent
```

### Changing Agent Permissions
1. Login to Keycloak Admin Console: http://localhost:8080/admin
2. Navigate to Users → `agent-<id>-m2m`
3. Go to Groups tab
4. Leave current group and join new group
5. Regenerate token: `python token_refresher.py --agent-id <id>`

### Viewing All Agents
1. Login to Keycloak Admin Console
2. Navigate to Users
3. Search for "agent-" to see all service accounts

---

## Troubleshooting

### Script Fails with "KEYCLOAK_ADMIN_PASSWORD not set"
**Solution:** Set the required environment variable:
```bash
export KEYCLOAK_ADMIN_PASSWORD="your-password"
```

### Can't Access Admin Console
**Check Keycloak is running:**
```bash
docker-compose ps keycloak
```

**Check logs:**
```bash
docker-compose logs keycloak
```

### Token Generation Fails
**Verify service account exists:**
1. Check in Keycloak Admin Console under Users
2. Or regenerate: `./keycloak/setup/setup-agent-service-account.sh --agent-id <id> --group <group>`

#### clean-keycloak.sh
**What it does:**
- Removes the entire `mcp-gateway` realm
- Deletes all service accounts and groups
- Provides a complete reset for testing or troubleshooting

**Required Environment Variables:**
- `KEYCLOAK_ADMIN_PASSWORD` - Admin password for Keycloak

**Usage:**
```bash
# WARNING: This will delete all Keycloak configuration
./keycloak/setup/clean-keycloak.sh
```

**When to use:**
- Starting fresh after configuration errors
- Testing setup scripts from scratch
- Removing all MCP Gateway configuration from Keycloak

---

## Next Steps

After setting up Keycloak:
1. Generate tokens for your agents: See [Token Management](../credentials-provider/README.md)
2. Configure your AI agents: See [Agent Configuration](../agents/README.md)
3. Test the integration: See [Testing Guide](../docs/testing.md)

For detailed documentation, see [Keycloak Integration Guide](../docs/keycloak-integration.md)
