# Complete Setup Guide: MCP Gateway & Registry from Scratch

This guide provides a comprehensive, step-by-step walkthrough for setting up the MCP Gateway & Registry on a fresh Amazon EC2 instance. Perfect for first-time users who want to get the system running from zero.

## Table of Contents
1. [Amazon EC2 Instance Setup](#1-amazon-ec2-instance-setup)
2. [Initial System Configuration](#2-initial-system-configuration)
3. [Installing Prerequisites](#3-installing-prerequisites)
4. [Cloning and Configuring the Project](#4-cloning-and-configuring-the-project)
5. [Setting Up Keycloak Identity Provider](#5-setting-up-keycloak-identity-provider)
6. [Starting the MCP Gateway Services](#6-starting-the-mcp-gateway-services)
7. [Verification and Testing](#7-verification-and-testing)
8. [Configuring AI Agents and Coding Assistants](#8-configuring-ai-agents-and-coding-assistants)
9. [Troubleshooting](#9-troubleshooting)
10. [Next Steps](#10-next-steps)

---

## 1. Amazon EC2 Instance Setup

### Launch EC2 Instance

1. **Log into the AWS Management Console** and navigate to EC2
2. **Click "Launch Instance"** and configure:
   - **Name**: `mcp-gateway-server`
   - **AMI**: Ubuntu Server 24.04 LTS (or latest Ubuntu LTS)
   - **Instance Type**: `t3.2xlarge` (8 vCPU, 32GB RAM)
   - **Key Pair**: Create new or select existing SSH key
   - **Storage**: 100GB gp3 SSD

3. **Network Settings**:
   - VPC: Default or your custom VPC
   - Subnet: Public subnet with auto-assign public IP
   - **Security Group**: Create new with following rules:
     ```
     Inbound Rules:
     - SSH (22): Your IP address
     - HTTP (80): 0.0.0.0/0 (or restrict as needed)
     - HTTPS (443): 0.0.0.0/0 (or restrict as needed)
     - Custom TCP (7860): 0.0.0.0/0 (Registry UI)
     - Custom TCP (8080): 0.0.0.0/0 (Keycloak Admin)
     - Custom TCP (8000): 0.0.0.0/0 (Auth Server)
     ```

4. **Launch the instance** and wait for it to be running

### Connect to Your Instance

```bash
# From your local terminal
ssh -i your-key.pem ubuntu@your-instance-public-ip

# Example:
ssh -i ~/.ssh/mcp-gateway-key.pem ubuntu@ec2-54-123-456-789.compute-1.amazonaws.com
```

---

## 2. Initial System Configuration

Once connected to your EC2 instance:

```bash
# Update system packages
sudo apt-get update && sudo apt-get upgrade -y

# Set timezone (optional but recommended)
sudo timedatectl set-timezone America/New_York  # Change to your timezone

# Create a working directory
mkdir -p ~/workspace
cd ~/workspace
```

---

## 3. Installing Prerequisites

### Install Docker and Docker Compose

```bash
# Install Docker
sudo apt-get install -y apt-transport-https ca-certificates curl software-properties-common

# Add Docker's official GPG key
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# Add Docker repository (for Ubuntu 24.04 Noble and later)
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list

# Update package list
sudo apt-get update

# Install Docker Engine and CLI
sudo apt-get install -y docker-ce docker-ce-cli containerd.io

# Add user to docker group
sudo usermod -aG docker $USER

# Apply the group change immediately for current shell
newgrp docker

# Verify Docker works without sudo
docker --version
# Expected output: Docker version 27.x.x or higher

# Test Docker permissions (MUST work without sudo)
docker run hello-world
# Should show "Hello from Docker!" message

# Install Docker Compose V2 Plugin (REQUIRED)
sudo apt-get install -y docker-compose-plugin

# Verify Docker Compose V2 installation
docker compose version
# Expected output: Docker Compose version v2.x.x or higher

# Note: The build_and_run.sh script requires Docker Compose V2 (docker compose)
# Do NOT use the old standalone docker-compose v1
```

### Install Node.js and npm

```bash
# Install Node.js 20.x (LTS)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Verify installations
node --version  # Should show v20.x.x
npm --version   # Should show 10.x.x
```

### Install Python and UV (Python Package Manager)

```bash
# Install Python 3.12
sudo apt-get install -y python3.12 python3.12-venv python3-pip

# Install UV package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Add UV to PATH
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Verify UV installation
uv --version
# Expected output: uv 0.x.x
```

### Install Additional Tools

```bash
# Install Git (should already be installed, but just in case)
sudo apt-get install -y git

# Install jq for JSON processing
sudo apt-get install -y jq

# Install curl and wget
sudo apt-get install -y curl wget

# Install net-tools for network debugging
sudo apt-get install -y net-tools
```

---

## 4. Cloning and Configuring the Project

### Clone the Repository

```bash
cd ~/workspace
git clone https://github.com/agentic-community/mcp-gateway-registry.git
cd mcp-gateway-registry

# Verify you're in the right directory
ls -la
# You should see files like docker-compose.yml, .env.example, README.md, etc.
```

### Setup Python Virtual Environment

```bash
# Create and activate Python virtual environment
uv sync
source .venv/bin/activate

# Verify the virtual environment is active
which python
# Should show: /home/ubuntu/workspace/mcp-gateway-registry/.venv/bin/python
```

### Initial Environment Configuration

```bash
# Copy the example environment file
cp .env.example .env

# Generate a secure SECRET_KEY and set it in the .env file
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
# Replace SECRET_KEY whether it's commented (#) or not
sed -i "s/^#*\s*SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env

# Verify the SECRET_KEY was set correctly
echo "Generated SECRET_KEY: $SECRET_KEY"

# Open the file for editing
nano .env
```

The SECRET_KEY has been automatically generated and added to your `.env` file. This key is essential for session security between the auth-server and registry services.

For now, make these additional essential changes in the `.env` file:

```bash
# Set authentication provider to Keycloak
AUTH_PROVIDER=keycloak #Do not change

# Set a secure admin password (change this!)
# This is used for Keycloak API authentication during setup
KEYCLOAK_ADMIN_PASSWORD=YourSecureAdminPassword123! # change me

# CRITICAL: Set INITIAL_ADMIN_PASSWORD to the SAME VALUE as KEYCLOAK_ADMIN_PASSWORD
# This is used to set the password for the initial admin user in the realm
# THESE MUST MATCH - see Step 5 for details
INITIAL_ADMIN_PASSWORD=YourSecureAdminPassword123! # change me

# Set Keycloak database password (change this!)
KEYCLOAK_DB_PASSWORD=SecureKeycloakDB123! # change me

# Leave other Keycloak settings as default for now
KEYCLOAK_URL=http://localhost:8080
KEYCLOAK_REALM=mcp-gateway
KEYCLOAK_CLIENT_ID=mcp-gateway-client

# Session Cookie Security Configuration
# CRITICAL: These settings must match your deployment environment

# For LOCAL DEVELOPMENT (accessing via http://localhost):
SESSION_COOKIE_SECURE=false  # MUST be false for HTTP access

# For PRODUCTION with HTTPS (accessing via https://your-domain.com):
# SESSION_COOKIE_SECURE=true  # Uncomment and set to true

# Cookie domain (leave empty for most deployments)
SESSION_COOKIE_DOMAIN=  # Empty = cookie scoped to exact host only

# Save and exit (Ctrl+X, then Y, then Enter)
```

**Important**:
- Remember the passwords you set here - you'll need to use the same ones in Step 5!
- **CRITICAL**: `KEYCLOAK_ADMIN_PASSWORD` and `INITIAL_ADMIN_PASSWORD` MUST be set to the same value. See Step 5 for details about why this is important.
- **SESSION_COOKIE_SECURE**: For local development (HTTP), this MUST be `false`. Setting it to `true` will cause login to fail because cookies with `secure=true` are only sent over HTTPS connections.
- For production deployments with HTTPS, change `SESSION_COOKIE_SECURE=true` before starting services.

### Download Required Embeddings Model

The MCP Gateway requires a sentence-transformers model for intelligent tool discovery. Download it to the shared models directory:

```bash
# Download the embeddings model (this may take a few minutes)
hf download sentence-transformers/all-MiniLM-L6-v2 --local-dir ${HOME}/mcp-gateway/models/all-MiniLM-L6-v2

# Verify the model was downloaded
ls -la ${HOME}/mcp-gateway/models/all-MiniLM-L6-v2/
# You should see model files like model.safetensors, config.json, etc.
```

**Note**: This command automatically creates the necessary directory structure and downloads all required model files (~90MB).

---

## 5. Setting Up Keycloak Identity Provider

Keycloak provides enterprise-grade authentication with support for both human users and AI agents.

### Set Keycloak Passwords

**Important**: These environment variables will override the values in your `.env` file. Use the SAME passwords you configured in Step 4!

```bash
# Use the SAME passwords you set in the .env file in Step 4!
# Replace these with your actual passwords from Step 4
export KEYCLOAK_ADMIN_PASSWORD="YourSecureAdminPassword123!"
export KEYCLOAK_DB_PASSWORD="SecureKeycloakDB123!"

# Verify they're set correctly
echo "Admin Password: $KEYCLOAK_ADMIN_PASSWORD"
echo "DB Password: $KEYCLOAK_DB_PASSWORD"
```

**Critical**: These passwords MUST match what you set in the `.env` file in Step 4. If they don't match, Keycloak initialization will fail!

### Important: Admin Password Configuration

When you set up Keycloak, you need to configure TWO admin password variables in your `.env` file:

1. **`KEYCLOAK_ADMIN_PASSWORD`** - Used to authenticate with the Keycloak admin API during initialization
2. **`INITIAL_ADMIN_PASSWORD`** - Used to set the password for the initial admin user created in the mcp-gateway realm

**These MUST be set to the SAME VALUE** for proper Keycloak initialization:

```bash
# In your .env file (Step 4), set these to the SAME password:
KEYCLOAK_ADMIN_PASSWORD=YourSecureAdminPassword123!
INITIAL_ADMIN_PASSWORD=YourSecureAdminPassword123!  # MUST match KEYCLOAK_ADMIN_PASSWORD
```

If these passwords don't match:
- The Keycloak admin user will be created with `INITIAL_ADMIN_PASSWORD`
- But API authentication during setup uses `KEYCLOAK_ADMIN_PASSWORD`
- This mismatch will cause authentication failures during realm initialization

**Best Practice**: Use the same secure password for both variables during setup.

### Start Keycloak and PostgreSQL

```bash
# Start only the database and Keycloak services first
docker compose up -d keycloak-db keycloak

# Check if services are starting
docker compose ps

# Monitor logs to see when Keycloak is ready
docker compose logs -f keycloak
# Wait for message: "Keycloak 25.x.x started in xxxms"
# Press Ctrl+C to exit logs when you see this message
```

**Important**: Wait at least 2-3 minutes for Keycloak to fully initialize before proceeding.

**Note about Health Status**: The Keycloak container may show as "unhealthy" in `docker ps` output when running in development mode. This is normal and won't affect functionality. You can verify Keycloak is working by running:
```bash
curl http://localhost:8080/realms/master
# Should return JSON with realm information
```

### Disable SSL Requirement for Master Realm
```bash
ADMIN_TOKEN=$(curl -s -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=${KEYCLOAK_ADMIN}" \
    -d "password=${KEYCLOAK_ADMIN_PASSWORD}" \
    -d "grant_type=password" \
    -d "client_id=admin-cli" | \
    jq -r '.access_token') && \
curl -X PUT "http://localhost:8080/admin/realms/master" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"sslRequired": "none"}'
```

### Initialize Keycloak Configuration

**Important**: This is a two-step process. The initialization script creates the realm and clients but does NOT save the credentials to files.

```bash
# Make the setup script executable
chmod +x keycloak/setup/init-keycloak.sh

# Step 1: Run the Keycloak initialization
./keycloak/setup/init-keycloak.sh

# Expected output:
# ✓ Waiting for Keycloak to be ready...
# ✓ Keycloak is ready!
# ✓ Logged in to Keycloak
# ✓ Created realm: mcp-gateway
# ✓ Created clients: mcp-gateway-web and mcp-gateway-m2m
# ... more success messages ...
# ✓ Client secrets generated!
#
# IMPORTANT: The script will tell you to run get-all-client-credentials.sh
# to retrieve and save the credentials. This is the next required step!

#Step 2: Disable SSL for Application Realm

ADMIN_TOKEN=$(curl -s -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=${KEYCLOAK_ADMIN}" \
    -d "password=${KEYCLOAK_ADMIN_PASSWORD}" \
    -d "grant_type=password" \
    -d "client_id=admin-cli" | \
    jq -r '.access_token') && \
curl -X PUT "http://localhost:8080/admin/realms/mcp-gateway" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"sslRequired": "none"}'

# Step 3: Retrieve and save all client credentials (REQUIRED)
chmod +x keycloak/setup/get-all-client-credentials.sh
./keycloak/setup/get-all-client-credentials.sh

# This will:
# - Connect to Keycloak and retrieve all client secrets
# - Save credentials to .oauth-tokens/keycloak-client-secrets.txt
# - Create individual JSON files: .oauth-tokens/<client-id>.json
# - Create individual env files: .oauth-tokens/<client-id>.env
# - Display a summary of all saved credentials

# Expected output:
# ✓ Admin token obtained
# ✓ Found and saved: mcp-gateway-web
# ✓ Found and saved: mcp-gateway-m2m
# Files created in: .oauth-tokens/
```

### Set Up Users and Service Accounts

After initializing Keycloak, run the bootstrap script to create default users and M2M service accounts for testing and management:

```bash
# Make the bootstrap script executable
chmod +x ./cli/bootstrap_user_and_m2m_setup.sh

# Run the bootstrap script
./cli/bootstrap_user_and_m2m_setup.sh
```

This script creates:
- **3 Keycloak groups**: `registry-users-lob1`, `registry-users-lob2`, `registry-admins`
- **6 users for different roles**:
  - **LOB1 users**: `lob1-bot` (M2M service account) and `lob1-user` (human user)
  - **LOB2 users**: `lob2-bot` (M2M service account) and `lob2-user` (human user)
  - **Admin users**: `admin-bot` (M2M service account) and `admin-user` (human user)

All credentials are automatically generated and saved to the `.oauth-tokens/` directory. User passwords default to the `INITIAL_USER_PASSWORD` value from your `.env` file.

**Next steps**:
- Review the generated credentials in `.oauth-tokens/`
- Configure appropriate access scopes in your `scopes.yml` file
- Use these credentials for testing M2M client flows and human user authentication
- Log in to the dashboard with human user accounts to verify access

### Create Your First AI Agent Account

```bash
# Make the agent setup script executable
chmod +x keycloak/setup/setup-agent-service-account.sh

# Create a test agent with full access
./keycloak/setup/setup-agent-service-account.sh \
  --agent-id test-agent \
  --group mcp-servers-unrestricted

# Create an agent for AI coding assistants (VS Code, cursor, etc.)
./keycloak/setup/setup-agent-service-account.sh \
  --agent-id ai-coding-assistant \
  --group mcp-servers-unrestricted

# Create an agent with restricted access for registry operations
./keycloak/setup/setup-agent-service-account.sh \
  --agent-id registry-operator \
  --group mcp-servers-restricted

# Note: The script does not display the credentials at the end.
# Your Client ID is: agent-test-agent-m2m

# Retrieve and save ALL client credentials (recommended):
./keycloak/setup/get-all-client-credentials.sh

# This will:
# - Retrieve credentials for ALL clients in the realm
# - Save all credentials to .oauth-tokens/keycloak-client-secrets.txt
# - Create individual JSON files: .oauth-tokens/<client-id>.json
# - Create individual env files: .oauth-tokens/<client-id>.env
# - Display a summary of all credentials saved

# Or to get just one specific client:
./keycloak/setup/get-agent-credentials.sh agent-test-agent-m2m
```

**Important**: Save the Client ID and Client Secret shown in the output. You'll need these to authenticate your AI agents.

### Update .env File with Client Secrets

**Critical Step**: After running `get-all-client-credentials.sh`, you MUST update your `.env` file with the retrieved client secrets:

```bash
# View the retrieved client secrets
cat .oauth-tokens/keycloak-client-secrets.txt

# You'll see output like:
# KEYCLOAK_CLIENT_ID=mcp-gateway-web
# KEYCLOAK_CLIENT_SECRET=JyJzW00JeUBaCmH9Z5xtYDhE2MsGqOSv
#
# KEYCLOAK_M2M_CLIENT_ID=mcp-gateway-m2m
# KEYCLOAK_M2M_CLIENT_SECRET=iCjPsMLLmet124K8b7FCfcEcRJ9bx4Oo

# Update your .env file with these exact secret values
nano .env

# Find and update these lines with the actual secret values from above:
# KEYCLOAK_CLIENT_SECRET=JyJzW00JeUBaCmH9Z5xtYDhE2MsGqOSv
# KEYCLOAK_M2M_CLIENT_SECRET=iCjPsMLLmet124K8b7FCfcEcRJ9bx4Oo

# Save and exit (Ctrl+X, then Y, then Enter)
```

**Note**: These secrets are auto-generated by Keycloak and are different each time you run `init-keycloak.sh`. Always use the latest values from `.oauth-tokens/keycloak-client-secrets.txt`.

### Generate Access Tokens for All Keycloak Users and Agents

Generate access tokens for all configured agents and users:

```bash
# Generate access tokens for all agents
./credentials-provider/keycloak/generate_tokens.py --all-agents
```

This will create access token files (both `.json` and `.env` formats) for all Keycloak service accounts in the `.oauth-tokens/` directory.

**Note**: If you want tokens to last longer than the default 5 minutes, see [Configure Token Lifetime](#configure-token-lifetime) before generating tokens.

### Verify Keycloak is Running

Open a web browser and navigate to:
```
http://localhost:8080
```

You should see the Keycloak login page. You can log in with:
- Username: `admin`
- Password: The `KEYCLOAK_ADMIN_PASSWORD` you set earlier

---

## 6. Starting the MCP Gateway Services

### Build and Start All Services

```bash
# Return to project directory
cd ~/workspace/mcp-gateway-registry

# Activate the virtual environment if not already active
source .venv/bin/activate

# Make the build script executable
chmod +x build_and_run.sh

# Build frontend and start all services using the build script
./build_and_run.sh

# This script will:
# - Check for Node.js and npm installation
# - Build the React frontend in the frontend/ directory
# - Create necessary local directories
# - Build Docker images
# - Start all services with docker-compose

# After the script completes, check all services are running
docker-compose ps

# Expected output should show all services as "Up":
# - keycloak-db
# - keycloak
# - auth-server
# - registry
# - nginx
# - Various MCP servers (mcp-weather, mcp-time, etc.)
```

### Monitor Service Logs

```bash
# View all logs
docker-compose logs -f

# Or view specific service logs
docker-compose logs -f auth-server
docker-compose logs -f registry
docker-compose logs -f nginx

# Press Ctrl+C to exit log viewing
```

### Wait for Services to Initialize

```bash
# Check if registry is ready
curl http://localhost:7860/health

# Expected output:
# {"status":"healthy","timestamp":"..."}
```

---

## 7. Verification and Testing

### Test the Registry Web Interface

1. Open your web browser and navigate to:
   ```
   http://localhost:7860
   ```

2. You should see the MCP Gateway Registry login page

3. Click "Login with Keycloak" and use these test credentials:
   - Username: `admin`
   - Password: The `KEYCLOAK_ADMIN_PASSWORD` you set

### Test with Python MCP Client

```bash
# Navigate to project root directory
cd ~/workspace/mcp-gateway-registry

# Activate the virtual environment if not already active
source .venv/bin/activate

# Source the agent credentials from the saved file
source .oauth-tokens/agent-test-agent-m2m.env

# Option 2: Or manually set the environment variables
# export CLIENT_ID="agent-test-agent-m2m"
# export CLIENT_SECRET="<get-from-.oauth-tokens/keycloak-client-secrets.txt>"
# export KEYCLOAK_URL="http://localhost:8080"
# export KEYCLOAK_REALM="mcp-gateway"

# Test basic connectivity
uv run python cli/mcp_client.py ping

# Expected output:
# ✓ M2M authentication successful
# Session established: 277bf44c7d474d9b9674e7cc8a5122c8
# {
#   "jsonrpc": "2.0",
#   "id": 2,
#   "result": {}
# }

# List available tools
uv run python cli/mcp_client.py list
# Expected: List of available MCP tools

# Test calling a simple tool to get current time
# Note: current_time_by_timezone is on the 'currenttime' server, not 'mcpgw'
uv run python cli/mcp_client.py --url http://localhost/currenttime/mcp call --tool current_time_by_timezone --args '{"tz_name":"America/New_York"}'
# Expected: Current time in JSON format

# Alternative: Use intelligent_tool_finder on mcpgw to find and call tools dynamically
uv run python cli/mcp_client.py call --tool intelligent_tool_finder --args '{"natural_language_query":"get current time in New York"}'
# This will automatically find and route to the correct server
```

### Refreshing Credentials

If your access tokens have expired or you need to regenerate credentials, you can use the credential generation script:

```bash
# Navigate to project root directory
cd ~/workspace/mcp-gateway-registry

# Regenerate all credentials
./credentials-provider/generate_creds.sh
```

**Note**: You may see errors related to "egress token" during credential generation. These errors can be safely ignored as they refer to external identity providers (IdPs) that are not yet configured. The local Keycloak credentials will be generated successfully.

### Test Intelligent Agent Demo

```bash
# Use the intelligent tool finder to discover tools with natural language
uv run python cli/mcp_client.py call --tool intelligent_tool_finder --args '{"natural_language_query":"What is the current time?"}'
# Expected: Tool discovery results with time-related tools

# You can also run a full agent with the comprehensive agent script
# Note: Use --mcp-registry-url to point to your local gateway
uv run python agents/agent.py --agent-name agent-test-agent-m2m --mcp-registry-url http://localhost/mcpgw/mcp --prompt "What's the current time in New York?"
# Expected: Natural language response with current time
```

---

## 8. Configuring AI Agents and Coding Assistants

### Configure OAuth Credentials

Before generating tokens, you need to configure your OAuth credentials. Follow the [Configuration Reference](configuration.md) for detailed parameter documentation.

```bash
cd ~/workspace/mcp-gateway-registry

# Configure OAuth credentials for external services (if needed)
cp credentials-provider/oauth/.env.example credentials-provider/oauth/.env
# Edit credentials-provider/oauth/.env with your provider credentials

# Configure AgentCore credentials (if using Amazon Bedrock AgentCore)
cp credentials-provider/agentcore-auth/.env.example credentials-provider/agentcore-auth/.env
# Edit credentials-provider/agentcore-auth/.env with your AgentCore credentials
```

### Generate Authentication Tokens and MCP Configurations

```bash
# Generate all authentication tokens and MCP configurations
./credentials-provider/generate_creds.sh

# This script will:
# 1. Generate Keycloak agent tokens for ingress authentication
# 2. Generate external provider tokens for egress authentication (if configured)
# 3. Generate AgentCore tokens (if configured)
# 4. Create MCP configuration files for AI coding assistants
# 5. Add no-auth services to the configurations
```

### Start Automatic Token Refresh Service

For production use, start the token refresh service to automatically maintain valid tokens. See the [Authentication Guide](auth.md) for detailed information about token lifecycle management.

```bash
# Start the background token refresh service
./start_token_refresher.sh

# Monitor the token refresh process
tail -f token_refresher.log
```

**Example Token Refresh Output:**
```
2025-09-17 03:09:43,391,p455210,{token_refresher.py:370},INFO,Successfully refreshed OAuth token: agent-test-agent-m2m-token.json
2025-09-17 03:09:43,391,p455210,{token_refresher.py:898},INFO,Token successfully updated at: /home/ubuntu/repos/mcp-gateway-registry/.oauth-tokens/agent-test-agent-m2m-token.json
2025-09-17 03:09:43,631,p455210,{token_refresher.py:341},INFO,Refreshing OAuth token for provider: keycloak
2025-09-17 03:09:43,778,p455210,{token_refresher.py:341},INFO,Refreshing OAuth token for provider: atlassian
2025-09-17 03:09:43,778,p455210,{token_refresher.py:903},INFO,Refresh cycle complete: 8/8 tokens refreshed successfully
2025-09-17 03:09:43,778,p455210,{token_refresher.py:907},INFO,Regenerating MCP configuration files after token refresh...
2025-09-17 03:09:43,781,p455210,{token_refresher.py:490},INFO,MCP configuration files regenerated successfully
```

### Generated Token Files and Configurations

After running `generate_creds.sh`, check the `.oauth-tokens/` directory for generated files:

```bash
# List all generated token files and configurations
ls -la .oauth-tokens/
```

**Key Files Generated:**
- **Agent Tokens**: `agent-*-m2m-token.json` and `agent-*-m2m.env` files for each Keycloak agent
- **External Service Tokens**: `*-egress.json` files for external providers (Atlassian, etc.)
- **AI Coding Assistant Configurations**:
  - `mcp.json` - Configuration for Claude Code/Roocode format
  - `vscode_mcp.json` - Configuration for VS Code format
- **Raw Token Files**: `ingress.json`, individual service token files

**Example AI Coding Assistant Configuration (mcp.json):**
```json
{
  "mcpServers": {
    "mcpgw": {
      "type": "streamable-http",
      "url": "https://mcpgateway.ddns.net/mcpgw/mcp",
      "headers": {
        "X-Authorization": "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
        "X-Client-Id": "agent-ai-coding-assistant-m2m",
        "X-Keycloak-Realm": "mcp-gateway",
        "X-Keycloak-URL": "http://localhost:8080"
      },
      "disabled": false,
      "alwaysAllow": []
    },
    "atlassian": {
      "type": "streamable-http",
      "url": "https://mcpgateway.ddns.net/atlassian/mcp",
      "headers": {
        "Authorization": "Bearer eyJraWQiOiJhdXRoLmF0bGFzc2lhbi5jb20tQUNDRVNTLTk0ZTczYTkw...",
        "X-Atlassian-Cloud-Id": "923a213e-e930-4359-be44-f4b164d3f269",
        "X-Authorization": "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
        "X-Client-Id": "agent-ai-coding-assistant-m2m",
        "X-Keycloak-Realm": "mcp-gateway",
        "X-Keycloak-URL": "http://localhost:8080"
      },
      "disabled": false,
      "alwaysAllow": []
    }
  }
}
```

### Configure VS Code / Cursor / Claude Code

For VS Code or similar editors, you'll need to:

1. Copy the configuration to your local machine:
   ```bash
   # From your local machine (not the EC2 instance)
   scp -i your-key.pem ubuntu@your-instance-ip:~/workspace/mcp-gateway-registry/.oauth-tokens/mcp.json ~/
   ```

2. Add to your editor's MCP settings:
   - VS Code: Add to `.vscode/settings.json`
   - Cursor: Add to cursor settings
   - Claude Code: Add to claude settings

### Create a Python Test Agent

```bash
cd ~/workspace/mcp-gateway-registry/agents

# Create a test configuration
cat > agent_config.json <<EOF
{
  "client_id": "test-agent",
  "client_secret": "<your-agent-secret>",
  "gateway_url": "http://localhost:8000"
}
EOF

# Install Python dependencies
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Run the test agent
uv run python agent.py --config agent_config.json
```

---

## 9. Troubleshooting

### Common Issues and Solutions

#### Services Won't Start
```bash
# Check Docker daemon
sudo systemctl status docker

# Restart Docker if needed
sudo systemctl restart docker

# Check for port conflicts
sudo netstat -tlnp | grep -E ':(80|443|7860|8080|8000)'

# Stop conflicting services if found
sudo systemctl stop apache2  # If Apache is running
```

#### Keycloak Initialization Fails
```bash
# Check Keycloak logs
docker-compose logs keycloak | tail -50

# Restart Keycloak
docker-compose restart keycloak

# Wait 2-3 minutes and retry initialization
./keycloak/setup/init-keycloak.sh
```

**Password Mismatch Issue**: If you see authentication failures during initialization:
1. Verify that `KEYCLOAK_ADMIN_PASSWORD` and `INITIAL_ADMIN_PASSWORD` are set to the SAME VALUE in your `.env` file
2. If they don't match, fix them:
   ```bash
   # Edit your .env file and ensure these match:
   nano .env
   # KEYCLOAK_ADMIN_PASSWORD=your-password
   # INITIAL_ADMIN_PASSWORD=your-password  (MUST be identical)
   ```
3. Restart Keycloak and try initialization again:
   ```bash
   docker-compose restart keycloak
   # Wait 2-3 minutes, then:
   ./keycloak/setup/init-keycloak.sh
   ```

#### Login Redirects Back to Login Page

**Most Common Cause**: Incorrect `SESSION_COOKIE_SECURE` setting

**Symptoms**:
- You enter username/password
- Page redirects back to login page without error message
- No session cookie is stored in browser

**Solution**:
1. Check your `.env` file:
   ```bash
   grep SESSION_COOKIE_SECURE .env
   ```

2. **For localhost (HTTP) access**:
   ```bash
   # MUST be false
   SESSION_COOKIE_SECURE=false
   ```

3. **For HTTPS access**:
   ```bash
   # MUST be true
   SESSION_COOKIE_SECURE=true
   ```

4. **Verify in browser dev tools**:
   - Open browser dev tools (F12)
   - Go to Application → Cookies → Your domain
   - Check if `mcp_gateway_session` cookie exists
   - For HTTP: `Secure` flag should be UNCHECKED
   - For HTTPS: `Secure` flag should be CHECKED

5. **After fixing, rebuild and restart**:
   ```bash
   docker compose down
   docker compose build --no-cache auth-server registry
   docker compose up -d
   ```

**Why this happens**: Cookies with `secure=true` are ONLY sent over HTTPS connections. If you access via HTTP (like `http://localhost:7860`), the browser will reject the cookie and login will fail.

#### Authentication Issues
```bash
# Verify Keycloak is accessible
curl http://localhost:8080/realms/mcp-gateway

# Check auth server logs
docker-compose logs auth-server | tail -50

# Regenerate agent credentials
./keycloak/setup/setup-agent-service-account.sh \
  --agent-id new-test-agent \
  --group mcp-servers-unrestricted
```

#### Login Redirects Back to Login Page
This usually indicates a session cookie issue between auth-server and registry:

```bash
# Check for SECRET_KEY mismatch
docker-compose logs auth-server | grep "SECRET_KEY"
docker-compose logs registry | grep -E "(session|cookie|Invalid)"

# If you see "No SECRET_KEY environment variable found", regenerate and restart:
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env

# Recreate containers to pick up new SECRET_KEY
docker-compose stop auth-server registry
docker-compose rm -f auth-server registry
docker-compose up -d auth-server registry

# Test login again - should work now
```

#### Configure Token Lifetime
By default, Keycloak generates tokens with a 5-minute (300 seconds) lifetime. To change this for longer-lived tokens:

**Method 1: Via Keycloak Admin Console**
1. Go to `http://localhost:8080/admin` (or your Keycloak URL)
2. Login with admin credentials
3. Select the `mcp-gateway` realm
4. Go to **Realm Settings** → **Tokens** → **Access Token Lifespan**
5. Change from `5 Minutes` to desired value (e.g., `1 Hour`)
6. Click **Save**

**Method 2: Via Keycloak Admin API**
```bash
# Get admin token
ADMIN_TOKEN=$(curl -s -X POST "http://localhost:8080/realms/master/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password&client_id=admin-cli&username=admin&password=your-keycloak-admin-password" | \
  jq -r '.access_token')

# Update access token lifespan to 1 hour (3600 seconds)
# Note: By default, Keycloak access tokens expire after 5 minutes
# Only increase this timeout if it's consistent with your organization's security policy
curl -X PUT "http://localhost:8080/admin/realms/mcp-gateway" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"accessTokenLifespan": 3600}'

# Verify the change
curl -X GET "http://localhost:8080/admin/realms/mcp-gateway" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.accessTokenLifespan'
```

**Note**: New tokens generated after this change will use the updated lifetime. Existing tokens retain their original expiration time.

#### OAuth2 Callback Failed
If you see "oauth2_callback_failed" error:

```bash
# Check Keycloak external URL configuration
docker-compose exec -T auth-server env | grep KEYCLOAK_EXTERNAL_URL
# Should show: KEYCLOAK_EXTERNAL_URL=http://localhost:8080

# If missing, add to .env file:
echo "KEYCLOAK_EXTERNAL_URL=http://localhost:8080" >> .env
docker-compose restart auth-server

# Check auth-server can reach Keycloak internally
docker-compose exec auth-server curl -f http://keycloak:8080/health/ready
```

#### Registry Not Loading
```bash
# Check registry logs
docker-compose logs registry | tail -50

# Rebuild registry frontend
cd ~/workspace/mcp-gateway-registry/registry
npm install
npm run build
cd ..
docker-compose restart registry
```

### View Real-time Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f <service-name>

# Last 100 lines
docker-compose logs --tail=100 <service-name>
```

### Stopping Services

```bash
# Graceful shutdown (keeps data)
docker-compose down

# Complete cleanup (removes all data)
docker-compose down -v

# Just stop services (to restart later)
docker-compose stop
```

### Reset Everything
If you need to start over completely:
```bash
# Stop all services and remove volumes
docker-compose down -v

# Remove all Docker images (optional)
docker system prune -a

# Start fresh
docker-compose up -d keycloak-db keycloak
# Then follow setup steps again from Step 5
```

---

## 10. Custom HTTPS Domain Configuration

If you're running this setup with a custom HTTPS domain (e.g., `https://mcpgateway.mycorp.com`) instead of localhost, you'll need to update the following parameters in your `.env` file:

### Parameters to Update for Custom HTTPS Domain

```bash
# Update these parameters in your .env file:

# 1. Registry URL - Replace with your custom domain
REGISTRY_URL=https://mcpgateway.mycorp.com

# 2. Auth Server External URL - Replace with your custom domain
AUTH_SERVER_EXTERNAL_URL=https://mcpgateway.mycorp.com

# 3. Keycloak External URL - Replace with your custom domain
KEYCLOAK_EXTERNAL_URL=https://mcpgateway.mycorp.com

# 4. Keycloak Admin URL - Replace with your custom domain
KEYCLOAK_ADMIN_URL=https://mcpgateway.mycorp.com
```

### Parameters to KEEP UNCHANGED

These parameters should remain as localhost/Docker network addresses for internal communication:

```bash
# DO NOT CHANGE - These are for internal Docker network communication:
AUTH_SERVER_URL=http://auth-server:8888
KEYCLOAK_URL=http://keycloak:8080
```

### Additional Considerations for Custom Domains

1. **SSL/TLS Certificates**: Ensure you have valid SSL certificates for your domain
2. **Firewall Rules**: Update security groups/firewall rules for your custom domain
3. **DNS Configuration**: Ensure your domain points to your server's public IP address

### Testing Custom Domain Setup

After updating your `.env` file with custom domain values:

```bash
# Restart services to pick up new configuration
docker-compose restart auth-server registry

# Test the custom domain
curl -f https://mcpgateway.mycorp.com/health

# Test Keycloak access
curl -f https://mcpgateway.mycorp.com/realms/mcp-gateway
```

---

## 11. Next Steps

### Secure Your Installation

1. **Update Security Groups**: Restrict IP access to only necessary addresses
2. **Enable HTTPS**: Set up SSL certificates for production use
3. **Change Default Passwords**: Update all default passwords in production
4. **Set up Monitoring**: Configure CloudWatch or similar monitoring

### Add More MCP Servers

1. Check available MCP servers:
   ```bash
   ls ~/workspace/mcp-gateway-registry/registry/servers/
   ```

2. Edit `docker-compose.yml` to enable additional servers

3. Restart services:
   ```bash
   docker-compose up -d
   ```

### Configure Production Settings

1. **Domain Name**: Set up a domain name and update configurations
2. **Load Balancer**: Add an Application Load Balancer for high availability
3. **Backup Strategy**: Implement regular backups of PostgreSQL database
4. **Scaling**: Consider EKS deployment for auto-scaling capabilities

### Explore Advanced Features

- **Fine-grained Access Control**: Configure `scopes.yml` for detailed permissions
- **Custom MCP Servers**: Add your own MCP server implementations
- **OAuth Integration**: Connect with external services (GitHub, Atlassian, etc.)
- **Monitoring Dashboard**: Set up Grafana for metrics visualization

### Documentation Resources

- [Authentication Guide](auth.md) - Deep dive into authentication options
- [Keycloak Advanced Configuration](keycloak-integration.md) - Enterprise features
- [API Reference](registry_api.md) - Programmatic registry management
- [Dynamic Tool Discovery](dynamic-tool-discovery.md) - AI agent capabilities
- [Production Deployment](production-deployment.md) - Best practices for production

### Getting Help

- **GitHub Issues**: https://github.com/agentic-community/mcp-gateway-registry/issues
- **Discussions**: https://github.com/agentic-community/mcp-gateway-registry/discussions
- **Documentation**: Check the `/docs` folder for detailed guides

---

## Container Publishing for Production Deployment

For production environments or to contribute pre-built images, you can publish the containers to Docker Hub and GitHub Container Registry.

### Publishing Script Overview

The `scripts/publish_containers.sh` script automates building and publishing all 6 container components:

- `registry` - Main registry service with nginx and web UI
- `auth-server` - Authentication service
- `currenttime-server` - Current time MCP server
- `realserverfaketools-server` - Example tools MCP server
- `fininfo-server` - Financial information MCP server
- `mcpgw-server` - MCP Gateway proxy server

### Publishing Commands

**Test build locally (no push):**
```bash
./scripts/publish_containers.sh --local
```

**Publish to Docker Hub:**
```bash
./scripts/publish_containers.sh --dockerhub
```

**Publish to GitHub Container Registry:**
```bash
./scripts/publish_containers.sh --ghcr
```

**Publish to both registries:**
```bash
./scripts/publish_containers.sh --dockerhub --ghcr
```

**Build specific component:**
```bash
./scripts/publish_containers.sh --dockerhub --component registry
```

### Required Environment Variables

Add these to your `.env` file for publishing:

```bash
# Container Registry Credentials
DOCKERHUB_USERNAME=aarora79
DOCKERHUB_TOKEN=your_docker_hub_token
GITHUB_TOKEN=your_github_token

# Organization names for publishing
DOCKERHUB_ORG=mcpgateway
GITHUB_ORG=agentic-community
```

### Generated Image Names

**Docker Hub (Organization Account):**
- `mcpgateway/registry:latest`
- `mcpgateway/auth-server:latest`
- `mcpgateway/currenttime-server:latest`
- `mcpgateway/realserverfaketools-server:latest`
- `mcpgateway/fininfo-server:latest`
- `mcpgateway/mcpgw-server:latest`

**GitHub Container Registry:**
- `ghcr.io/agentic-community/mcp-registry:latest`
- `ghcr.io/agentic-community/mcp-auth-server:latest`
- `ghcr.io/agentic-community/mcp-currenttime-server:latest`
- `ghcr.io/agentic-community/mcp-realserverfaketools-server:latest`
- `ghcr.io/agentic-community/mcp-fininfo-server:latest`
- `ghcr.io/agentic-community/mcp-mcpgw-server:latest`

### Using Pre-built Images

Once published, anyone can use the pre-built images with:

```bash
# Use the pre-built deployment option
./build_and_run.sh --prebuilt
```

This deployment method:
- Skips the build process entirely
- Pulls pre-built images from container registries
- Starts services in under 2 minutes
- Requires no Node.js or build dependencies

---

## Summary

You now have a fully functional MCP Gateway & Registry running on your Amazon EC2 instance! The system is ready to:

- Authenticate AI agents and human users through Keycloak
- Provide centralized access to MCP servers
- Enable dynamic tool discovery for AI assistants
- Offer a web-based registry for managing configurations

Remember to:
- Save all generated credentials securely
- Monitor service logs regularly
- Keep the system updated with latest releases
- Follow security best practices for production use

Congratulations on completing the setup! Your enterprise MCP gateway is now operational and ready to serve both AI agents and development teams.
