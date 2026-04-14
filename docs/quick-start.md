# Quick Start Guide

Get the Jarvis Registry running in 5 minutes with this streamlined setup guide.

## What You'll Accomplish

By the end of this guide, you'll have:

- ✅ Jarvis Registry running locally
- ✅ Authentication configured with Entra ID
- ✅ Access to curated enterprise MCP tools

## Prerequisites

- **Identity Provider**: Microsoft Entra ID  or Keycloak (see [Entra ID setup](entra-id-setup.md) or [Keycloak Integration](keycloak-integration.md))
- **Container Runtime**: Docker and Docker Compose installed
- **Basic Command Line**: Comfort with terminal/command prompt

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

# Enable Entra ID in OAuth2 providers (set to true when using Entra ID)
ENTRA_ENABLED=true
```

## Step 3: Generate Secret Keys

Use the interactive **[Generate Secrets](generate-secrets.md)** page to create your `SECRET_KEY`, `CREDS_KEY`, `JWT_PRIVATE_KEY`, and `JWT_PUBLIC_KEY` directly in the browser — no CLI required. Copy the `.env` output block and paste it into your `.env` file.

```bash
# Open .env file for editing
vim .env
```

## Step 4: Deploy Services

```bash
# Start all services
docker compose --profile full up -d

# Open the registry UI
open http://localhost:80
```

⏱️ **This takes about 2-3 minutes** - Container images will be pulled and services started.

## Step 5: Verify Installation

```bash
# Check all services are running
docker compose ps

# You should see services like:
# - auth-server (port 8888)
# - grafana (port 3000)
# - otel-collector (ports 4317, 4318, 8889)
# - prometheus (port 9090)
# - registry (port 7860)
# - registry-frontend (ports 80/443)
# - weaviate (ports 8099, 50051)
# - mongodb (port 27017)
# - redis (port 6379)
```

**Access the web interface:**

```bash
# Open in browser
open http://localhost:80
```

Use your Entra ID to login 


## 🎉 Success! What's Next?

You now have a fully functional Jarvis Registry! Here are your next steps:

### Immediate Next Steps
- 🔍 **Explore the Web Interface** - Browse available MCP servers and tools
- 🤖 **Try AI Assistant Integration** - Use tools through VS Code or your preferred AI assistant
- 🛠️ **Add Your Own MCP Servers** - Register custom tools for your team

### Expand Your Setup
- 📚 **[Full Installation Guide](installation.md)** - Production deployment options
- 🔐 **[Authentication Setup](auth.md)** - Advanced identity provider configuration
- 🎯 **[AI Assistants Guide](ai-coding-assistants-setup.md)** - Connect more development tools

### Enterprise Features
- 👥 **[Fine-Grained Access Control](scopes.md)** - Team-based permissions
- 📊 **[Monitoring & Analytics](monitoring.md)** - Usage tracking and health monitoring
- 🏢 **[Production Deployment](production-deployment.md)** - High availability and scaling

## Troubleshooting Quick Fixes

### Services Won't Start
```bash
# Check Docker daemon
sudo systemctl status docker
sudo systemctl start docker

# Check port conflicts
sudo netstat -tlnp | grep -E ':(80|443|7860|8080)'
```

### Can't Access Web Interface
```bash
# Check if registry is running
curl http://localhost:7860/health

# Check logs
docker-compose logs registry
```

## Getting Help

- 📖 **[Full Documentation](/)** - Comprehensive guides and references
- 🐛 **[GitHub Issues](https://github.com/agentic-community/mcp-gateway-registry/issues)** - Bug reports and feature requests
- 💬 **[GitHub Discussions](https://github.com/agentic-community/mcp-gateway-registry/discussions)** - Community support and questions
- 📧 **[Troubleshooting Guide](troubleshooting.md)** - Common issues and detailed solutions

---

**🎯 Pro Tip:** Once you have the basic setup working, explore the [AI Coding Assistants Setup Guide](ai-coding-assistants-setup.md) to connect additional development tools like Cursor, Claude Code, and Cline for a complete enterprise AI development experience!
