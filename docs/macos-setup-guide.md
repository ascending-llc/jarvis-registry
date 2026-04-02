# Complete macOS Setup Guide: MCP Gateway & Registry

This guide provides a comprehensive, step-by-step walkthrough for setting up the MCP Gateway & Registry on macOS. Perfect for local development and testing.

## Table of Contents
1. [Prerequisites](#1-prerequisites)
2. [Cloning and Initial Setup](#2-cloning-and-initial-setup)
3. [Environment Configuration](#3-environment-configuration)
4. [Starting All Services](#4-starting-all-services)
5. [Verification and Testing](#5-verification-and-testing)
6. [Troubleshooting](#6-troubleshooting)

## 1. Prerequisites

### System Requirements
- **macOS**: 12.0 (Monterey) or later
- **RAM**: At least 8GB (16GB recommended)
- **Storage**: At least 10GB free space
- **Administrator Access**: Sudo privileges required for Docker volume setup

### Required Software

**Container Runtime (choose one):**
- **Docker Desktop**: Install from https://www.docker.com/products/docker-desktop/
  - Includes Docker Compose
  - Requires privileged port access
  - **Important**: Make sure Docker Desktop is running before proceeding!
- **Podman Desktop** (Alternative, recommended for rootless): Install from https://podman-desktop.io/ or via Homebrew
  - Rootless container execution
  - No privileged port requirements
  - See [Podman Deployment](#10-podman-deployment) section below

**Other Requirements:**
- **Node.js**: Version 20.x LTS - Install from https://nodejs.org/ or via Homebrew (not needed with `--prebuilt` flag)
- **Python**: Version 3.12+ - Install via Homebrew (`brew install python@3.12`)
- **UV Package Manager**: Install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Git**: Usually pre-installed on macOS
- **jq**: Install via Homebrew (`brew install jq`)

---

## 2. Container Runtime Choice

Choose between Docker and Podman based on your needs:

### Docker (Default)
✅ Best for: Standard deployment, familiar workflow
✅ Uses privileged ports (80, 443)
✅ Access at `http://localhost`
⚠️ Requires Docker daemon running

### Podman (Rootless Alternative)
✅ Best for: Rootless deployment, no Docker daemon
✅ Uses non-privileged ports (8080, 8443)
✅ Access at `http://localhost:8080`
✅ More secure, no root access needed

**This guide uses Docker by default**. For Podman-specific instructions, see [Section 10: Podman Deployment](#10-podman-deployment).

---

## 3. Cloning and Initial Setup

### Clone the Repository
```bash
# Create workspace directory
mkdir -p ~/workspace
cd ~/workspace

# Clone the repository
git clone https://github.com/agentic-community/mcp-gateway-registry.git
cd mcp-gateway-registry

# Verify you're in the right directory
ls -la
# Should see: docker-compose.yml, .env.example, README.md, etc.
```

### Setup Python Virtual Environment
```bash
# Create and activate Python virtual environment
uv sync --all-packages
source .venv/bin/activate

# Verify virtual environment is active
which python
# Should show: /Users/[username]/workspace/mcp-gateway-registry/.venv/bin/python
```

---

## 4. Environment Configuration

### Create Environment File
```bash
# Copy the example environment file
cp .env.example .env

# Generate a secure SECRET_KEY
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
echo "Generated SECRET_KEY: $SECRET_KEY"

# Open .env file for editing
nano .env
```

### Configure Authentication Provider

__Note__: This guide documents the steps needed to use Microsoft Entra ID as the authentication provider. To configure an alternate provider, please reference either the [Keycloak integration](/docs/keycloak-integration.md) or [Cognito integration](/docs/cognito.md) guide.

In the `.env` file, make the following changes:
```bash
# Set authentication provider to Microsoft Entra ID
AUTH_PROVIDER=entra

# Set Entra ID Settings
ENTRA_TENANT_ID=your_tenant_id
ENTRA_CLIENT_ID=your_client_id
ENTRA_CLIENT_SECRET=your_client_secret

# Set your generated SECRET_KEY
SECRET_KEY=[paste-your-generated-key-here]
```

### Download Required Embeddings Model

The MCP Gateway requires a sentence-transformers model for intelligent tool discovery. Download it to the shared models directory:

```bash
# Download the embeddings model (this may take a few minutes)
huggingface-cli download sentence-transformers/all-MiniLM-L6-v2 --local-dir ${HOME}/mcp-gateway/models/all-MiniLM-L6-v2

# Verify the model was downloaded
ls -la ${HOME}/mcp-gateway/models/all-MiniLM-L6-v2/
# You should see model files like model.safetensors, config.json, etc.
```

**Note**: This command automatically creates the necessary directory structure and downloads all required model files (~90MB). If you don't have `huggingface-cli` command installed, install it first with `uv pip install huggingface_hub[cli]` or `uv tool install huggingface-cli`.

### Import Database Schemas (Optional for Local Dev)

If you need to import database schemas from the jarvis-api repository for local development, you can do so using the import-schemas tool:

```bash
# Authenticate with GitHub CLI (for private repository access)
gh auth login

# Run the following commands from project root.
uv run --package registry-pkgs import-schemas \
--tag asc0.5.1 \
--output-dir ./registry-pkgs/src/registry_pkgs/models \
--token $(gh auth token)

# Verify schemas were imported
ls -la ./registry-pkgs/src/registry_pkgs/models/_generated
```

**Important Notes**:
- In CI/CD, the schemas will be automatically imported before generating Python wheels.
  Then the wheels that contain generated model code will be used for Docker builds.
- When `--files` is omitted, **all .json files** from the release will be imported

### Create Docker Compose Override File

```bash
cp docker-compose.override.yml.example docker-compose.override.yml

# open docker compose override file for editing
nano docker-compose.override.yml
```

### Configure Docker Compose

```yaml
  # Example: Disable Keycloak if using Entra ID
  keycloak:
    profiles:
      - disabled
    keycloak-db:
      profiles:
      - disabled

  # Example: Disable Frontend container if running Vite Server
  # registry-frontend:
  #  profiles:
  #   - disabled
```

## 4. Starting All Services

### Set Schema Import Variables (Required for Registry Build)

Before building the registry container, you need to set the schema version and GitHub token:

```bash
# Add to your .env file (recommended)
echo "SCHEMA_VERSION=asc0.4.0" >> .env

# Set GitHub token from gh auth (do NOT add to .env file for security)
export GITHUB_TOKEN=$(gh auth token)

```

### Start Services with Docker Compose

```bash
# Build and start all services (with schema import)
GITHUB_TOKEN=$(gh auth token) docker compose --profile full up --build -d

# Or if GITHUB_TOKEN is already exported:
docker compose --profile full up --build -d
```

### Start everything but frontend and registry

```bash
docker compose --profile dev up -d
```

**Important macOS Docker Volume Sharing**: On macOS, Docker Desktop only shares certain directories by default (like `/Users`, `/tmp`, `/private`). The `/opt` and `/var/log` directories we need are NOT shared by default, so we must create them with proper ownership for Docker containers to access them.

**Note**: If you encounter permission issues, you may need to add `/opt` to Docker Desktop's shared directories:
1. Open Docker Desktop
2. Go to Settings > Resources > Virtual file shares
3. Add `/opt` to the list of shared directories
4. Click "Apply & Restart"


### Verify All Services are Running
```bash
# Check all services status
docker compose ps

# Expected services (all should show "Up"):
# - auth-server
# - registry
# - registry-frontend
# - mongodb
# - currenttime-server
# - fininfo-server
# - mcpgw-server
# - realserverfaketools-server
```

### Seed MongoDB with Sample Data

After starting the services, you can populate MongoDB with sample data including users, API keys, tokens, and MCP servers:

```bash
# Make sure MongoDB is running

# Seed the database with sample data
uv run seed-data

# sync data into vector database(optional)
uv run sync-mongo-weaviate
```

**Clean the database:**

```bash
# Remove all seeded data
uv run seed-data clean
```

**Environment Configuration:**
The seed script uses `MONGO_URI` from your `.env` file. Default value:
```bash
MONGO_URI=mongodb://localhost:27017/jarvis
```

### Monitor Service Logs
```bash
# View all logs
docker compose logs -f

# View specific service logs
docker compose logs -f auth-server
docker compose logs -f registry

# Press Ctrl+C to exit log viewing
```

### Stop the services

```bash
docker compose --profile full down
```

---

## 5. Verification and Testing

### Test Web Interface
1. **Open your web browser** and navigate to:
   ```
   http://localhost
   ```

2. **Login Page**: You should see the MCP Gateway Registry login page

3. **Login with Entra ID**: Click "Login with Entra ID" and use:

### Test API Access
```bash
# Test registry health
curl http://localhost/health
# Expected: {"status":"healthy","timestamp":"..."}

```

### Test Python MCP Client
```bash
# Activate virtual environment
source .venv/bin/activate

# Load agent credentials
source .oauth-tokens/agent-test-agent-m2m.env

# Test connectivity
uv run cli/mcp_client.py ping

# Expected output:
# ✓ M2M authentication successful
# Session established: [session-id]
# {"jsonrpc": "2.0", "id": 2, "result": {}}

# List available tools
uv run cli/mcp_client.py list

# Test a simple tool
uv run cli/mcp_client.py --url http://localhost/currenttime/mcp call --tool current_time_by_timezone --args '{"tz_name":"America/New_York"}'
```

## 10. Podman Deployment

This section provides complete instructions for deploying MCP Gateway & Registry using **Podman** instead of Docker on macOS. Podman offers rootless container execution without requiring privileged port access.

### Why Podman?

- ✅ **Rootless Execution**: No sudo or root access required
- ✅ **No Privileged Ports**: Uses ports 8080/8443 instead of 80/443
- ✅ **Enhanced Security**: Better container isolation
- ✅ **No Daemon**: Unlike Docker, Podman doesn't require a background daemon
- ✅ **Docker-Compatible**: Similar CLI commands and Compose support

### Installation

**Option 1: Podman Desktop (Recommended)**

```bash
# Install via Homebrew
brew install podman-desktop

# Launch Podman Desktop from Applications
# Or download from: https://podman-desktop.io/
```

**Option 2: Podman CLI Only**

```bash
# Install Podman
brew install podman

# Install additional tools
brew install podman-compose
```

### Initialize Podman Machine

Podman on macOS runs containers in a lightweight Linux VM:

```bash
# Initialize Podman machine with adequate resources
podman machine init --cpus 4 --memory 8192 --disk-size 50

# Start the machine
podman machine start

# Verify installation
podman --version
podman compose version
podman machine list
```

**Expected output:**
```
NAME                     VM TYPE     CREATED      LAST UP            CPUS        MEMORY      DISK SIZE
podman-machine-default*  qemu        2 hours ago  Currently running  4           8GiB        50GiB
```

### Complete Setup with Podman

Follow the same steps as the Docker guide (Sections 3-8), but use Podman commands:

**1. Clone and Configure (same as Section 3-4)**

```bash
# Clone repository
git clone https://github.com/agentic-community/mcp-gateway-registry.git
cd mcp-gateway-registry

# Configure environment
cp .env.example .env
nano .env
```

**2. Start Keycloak with Podman**

```bash
# Set passwords (must match .env file)
export KEYCLOAK_ADMIN_PASSWORD='your-admin-password'
export KEYCLOAK_DB_PASSWORD='your-db-password'

# Start Keycloak services
podman compose up -d keycloak-db keycloak

# Wait for services (takes ~60 seconds)
podman compose ps

# Follow logs
podman compose logs -f keycloak
```

**3. Configure Keycloak (same as Section 5-6)**

```bash
# Disable SSL requirement
podman exec mcp-gateway-registry-keycloak-1 /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master \
  --user admin --password "${KEYCLOAK_ADMIN_PASSWORD}"

podman exec mcp-gateway-registry-keycloak-1 /opt/keycloak/bin/kcadm.sh \
  update realms/master -s sslRequired=NONE

# Run Keycloak setup scripts
cd keycloak/setup
./create-realm-and-clients.sh
./get-all-client-credentials.sh

# Create test agent
./setup-agent-service-account.sh test-agent-1 registry-users-lob1
cd ../..
```

**4. Deploy All Services with Podman**

```bash
# Deploy using pre-built images (recommended)
./build_and_run.sh --prebuilt --podman

# Or build locally
./build_and_run.sh --podman
```

**The script automatically:**
- Detects Podman usage
- Applies `docker-compose.podman.yml` overlay
- Maps ports to non-privileged equivalents (8080/8443)
- Configures volume mounts with proper SELinux labels

### Access Services

**Important**: With Podman, services use different host ports:

| Service | URL (Podman) |
|---------|-------------|
| **Main UI** | `http://localhost:8080` |
| **Main UI (HTTPS)** | `https://localhost:8443` |
| Registry API | `http://localhost:7860` |
| Keycloak Admin | `http://localhost:18080/admin` |
| Auth Server | `http://localhost:8888` |
| Prometheus | `http://localhost:9090` |
| Grafana | `http://localhost:3000` |

**Open in browser:**
```bash
# Main interface (note port 8080)
open http://localhost:8080

# Registry API (unchanged)
open http://localhost:7860

# Keycloak admin console
open http://localhost:18080/admin
```

### Podman-Specific Commands

**Container Management:**

```bash
# List running containers
podman compose ps
# or: podman ps

# View logs
podman compose logs -f
podman compose logs -f registry
podman logs mcp-gateway-registry-registry-1

# Stop services
podman compose down

# Restart service
podman compose restart registry

# Execute commands in container
podman exec -it mcp-gateway-registry-registry-1 bash
```

**Resource Management:**

```bash
# View resource usage
podman stats

# Check Podman machine resources
podman machine inspect podman-machine-default

# Adjust machine resources (requires restart)
podman machine stop
podman machine rm
podman machine init --cpus 8 --memory 16384 --disk-size 100
podman machine start
```

**Volume Management:**

```bash
# List volumes
podman volume ls

# Inspect volume
podman volume inspect mcp-gateway-registry_metrics-db-data

# Remove unused volumes
podman volume prune
```

### Testing with Podman

Update test scripts to use Podman ports:

```bash
# Test registry health
curl http://localhost:7860/health

# Test main interface (note port 8080)
curl http://localhost:8080/

# Test with MCP client
cd cli
python mcp_client.py \
  --url http://localhost/mcpgw/mcp \
  --token-file ../.oauth-tokens/agent-test-agent-1-m2m.env \
  --command ping
```

### Troubleshooting Podman

**Issue: Podman machine won't start**

```bash
# Check status
podman machine list

# View machine logs
podman machine ssh systemctl status

# Reset machine
podman machine stop
podman machine rm
podman machine init --cpus 4 --memory 8192
podman machine start
```

**Issue: Port 8080 already in use**

```bash
# Check what's using the port
lsof -i :8080

# Option 1: Stop conflicting service
# Option 2: Edit docker-compose.podman.yml to use different ports
nano docker-compose.podman.yml
# Change "8080:80" to "8081:80"
```

**Issue: Permission denied on volumes**

```bash
# Ensure directories exist
mkdir -p ${HOME}/mcp-gateway/{servers,agents,models,logs}

# Check permissions
ls -la ${HOME}/mcp-gateway/

# Fix if needed
chmod -R 755 ${HOME}/mcp-gateway/
```

**Issue: Containers fail to start**

```bash
# Check logs
podman compose logs

# Verify machine has enough resources
podman machine inspect | grep -A5 "Resources"

# Increase if needed (see Resource Management above)
```

**Issue: podman compose command not found**

```bash
# Install podman-compose
pip install podman-compose

# Or install via Homebrew
brew install podman-compose

# Verify
podman compose version
```

### Switching Between Docker and Podman

You can switch between Docker and Podman without changing configurations:

```bash
# Stop Docker services
docker compose down

# Start with Podman
./build_and_run.sh --prebuilt --podman

# Or vice versa:
podman compose down
./build_and_run.sh --prebuilt
```

**Note**: Database volumes and configurations are separate between Docker and Podman. You'll need to reconfigure Keycloak when switching.

### Performance Considerations

**Podman Machine on macOS:**
- Runs in a QEMU VM (like Docker Desktop)
- Performance similar to Docker Desktop
- Recommended: 4+ CPUs, 8GB+ RAM
- SSD recommended for disk operations

**Tips for Better Performance:**
1. Allocate sufficient resources to Podman machine
2. Use `--prebuilt` flag to avoid local builds
3. Keep Podman Desktop updated
4. Use SSD for Podman machine storage

---

## 11. Troubleshooting
>>>>>>> fork-sync

### Common macOS Issues

#### Docker/Podman Not Running

**Docker:**
```bash
# Check if Docker is running
docker ps

# If error, start Docker Desktop from Applications
# Wait for whale icon to appear in menu bar
```

**Podman:**
```bash
# Check if Podman machine is running
podman machine list

# If not running, start it
podman machine start

# Verify
podman ps
```

#### Port Conflicts
```bash
# Check what's using ports
lsof -i :80
lsof -i :8080
lsof -i :7860

# Kill conflicting processes if needed
sudo lsof -ti :80 | xargs kill
```

#### Services Won't Start
```bash
# Check Docker memory/CPU limits in Docker Desktop preferences
# Recommended: 4GB RAM, 2 CPUs minimum

# Check disk space
df -h

# Restart all services
docker compose down
docker compose up -d
```

### Reset Everything
If you need to start over completely:
```bash
# Stop and remove all containers and data
docker compose down -v

# Remove Docker images (optional)
docker system prune -a

# Start fresh from Section 3
cp .env.example .env
cp .docker-compose.override.yml.example .docker-compose.override.yml
```

### View Service Status
```bash
# Check all service status
docker compose ps

# Check specific service health
docker compose logs [service-name] --tail 50

# Check resource usage
docker stats
```

### macOS-Specific Logs
```bash
# Check Console.app for system logs
# Check Docker Desktop logs via Docker Desktop > Troubleshoot > Get support

# Check local network issues
ping localhost
telnet localhost 8080
```

---

## Summary

You now have a fully functional MCP Gateway & Registry running on macOS! The system provides:

- **Authentication**: Micosoft Entra ID identity provider
- **Registry**: Web-based interface for managing MCP servers
- **API Gateway**: Centralized access to multiple MCP servers
- **Agent Support**: Ready for AI coding assistants and agents
- **Container Choice**: Works with both Docker and Podman

### Key URLs:

**With Docker:**
- **Registry**: http://localhost
- **API Gateway**: http://localhost/mcpgw/mcp
- **Individual Services**: http://localhost/[service-name]/mcp

**With Podman:**
- **Registry**: http://localhost:8080
- **Keycloak Admin**: http://localhost:18080/admin
- **API Gateway**: http://localhost:8080/mcpgw/mcp
- **Individual Services**: http://localhost:8080/[service-name]/mcp

### Key Files:
- **Configuration**: `.env`
- **MCP Server Data**: `registry/servers/*.json`
- **Agent Tokens**: `.oauth-tokens/agent-*-m2m.env`
- **Podman Overlay**: `docker-compose.podman.yml` (auto-applied with `--podman` flag)

### Next Steps:
1. **Configure your AI coding assistant** with the generated MCP configuration
2. **Create additional agents** using the setup-agent-service-account.sh script
3. **Add custom MCP servers** by editing docker-compose.yml
4. **Explore the web interface** to manage servers and view metrics
5. **Try Podman** if you want rootless container deployment (see Section 10)

**Remember**: Save your credentials securely and keep Docker Desktop running when using the system!

### Getting Help
- **Documentation**: Check `/docs` folder for additional guides
- **Logs**: Always check `docker compose logs` for troubleshooting
