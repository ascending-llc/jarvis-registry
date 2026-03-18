# Importing Servers from Anthropic MCP Registry

This guide explains how to import MCP servers from [Anthropic's official MCP Registry](https://registry.modelcontextprotocol.io/) into your MCP Gateway.

## Overview

The Anthropic MCP Registry is an open, collaboratively governed directory of Model Context Protocol (MCP) servers. It is maintained by Anthropic in partnership with GitHub and the wider community through an open-source contribution model. This registry provides a curated catalog of publicly available and community-contributed MCP servers. Its API enables MCP clients and gateways to discover and import server configurations automatically, simplifying integration and discovery workflows for developers.

The import functionality allows you to quickly add these servers to your gateway without manual configuration.

## Prerequisites

- MCP Gateway up and running
- Access to the registry container or CLI tools
- Environment variables configured in `.env` file (for authenticated servers)

> **Note**: The Anthropic API version is defined in `registry/core/config.py` as `Settings.anthropic_api_version` for easy version management.

## Quick Start

### Import a Single Server

```bash
cd /home/ubuntu/repos/mcp-gateway-registry
./cli/import_from_anthropic_registry.sh ai.smithery/smithery-ai-github
```

### Import Multiple Servers from a List

Create or edit `cli/import_server_list.txt`:

```text
# Popular MCP Servers
ai.smithery/smithery-ai-github
io.github.jgador/websharp
ai.smithery/Hint-Services-obsidian-github-mcp
```

Then import all servers in the list:

```bash
./cli/import_from_anthropic_registry.sh --import-list cli/import_server_list.txt
```

## Import Script Features

### Automatic Environment Variable Substitution

The import script automatically:
- Loads environment variables from `.env` file
- Substitutes authentication header placeholders with actual values
- Stores the final configuration with real credentials in JSON files

**Example:**
```json
// Before substitution (from Anthropic registry):
{
  "headers": [
    {
      "Authorization": "Bearer {smithery_api_key}"
    }
  ]
}

// After import (stored in gateway):
{
  "headers": [
    {
      "Authorization": "Bearer 3899299d-b7a2-471d-a185-200b9e9adcb2"
    }
  ]
}
```

### Server Name Transformation

Server names from the Anthropic registry are automatically transformed to work with the gateway:

- Slashes (`/`) are replaced with hyphens (`-`)
- Example: `ai.smithery/github` becomes `ai.smithery-github`
- The path is set to `/ai.smithery-github`

### Automatic Configuration

The import script automatically configures:
- **Server name** and **description** from registry
- **Proxy URL** to the remote server
- **Authentication type** (oauth, api-key, or none)
- **Authentication provider** (Keycloak for oauth servers)
- **Transport type** (streamable-http)
- **Tags** for discovery and organization
- **Headers** with substituted credentials

## Command Reference

### Basic Usage

```bash
./cli/import_from_anthropic_registry.sh [OPTIONS] [SERVER_NAME]
```

### Options

- `--import-list <file>` - Import servers from a file (one server name per line)
- `--dry-run` - Show what would be imported without actually importing
- `--gateway-url <url>` - Override gateway URL (default: http://localhost)
- `--base-port <port>` - Override base port for local servers (default: 8100)

### Examples

**Import with dry run:**
```bash
./cli/import_from_anthropic_registry.sh --dry-run ai.smithery/smithery-ai-github
```

**Import from custom list:**
```bash
./cli/import_from_anthropic_registry.sh --import-list my-servers.txt
```

**Import to remote gateway:**
```bash
GATEWAY_URL="https://mcpgateway.example.com" ./cli/import_from_anthropic_registry.sh ai.smithery/smithery-ai-github
```

## Server List File Format

Create a text file with one server name per line:

```text
# Lines starting with # are comments
# Empty lines are ignored

# GitHub API access
ai.smithery/smithery-ai-github

# Web search and article extraction
io.github.jgador/websharp

# Obsidian vault integration
ai.smithery/Hint-Services-obsidian-github-mcp
```

## Authentication Setup

### For Servers Requiring Authentication

1. **Get API Keys**: Obtain API keys from the service provider
   - Smithery servers: Visit [smithery.ai](https://smithery.ai)
   - Other services: Check their documentation

2. **Add to .env file**:
```bash
# Smithery API Key
SMITHERY_API_KEY=your-api-key-here

# Other service keys
OTHER_SERVICE_API_KEY=your-other-key
```

3. **Import servers**: The script automatically substitutes the keys

### Supported Authentication Types

The import script recognizes and configures:

- **OAuth/Bearer tokens**: `Authorization: Bearer {api_key}`
- **API keys**: `X-API-Key: {api_key}` or `API-Key: {api_key}`
- **Custom headers**: Other authentication header formats

## Finding Servers to Import

### Browse Anthropic's MCP Registry

Visit [registry.modelcontextprotocol.io](https://registry.modelcontextprotocol.io/) to:
- Browse available servers
- View server capabilities and tools
- Check authentication requirements
- Read documentation

### List Servers via API

```bash
# List all available servers
curl https://registry.modelcontextprotocol.io/v0.1/servers | jq '.servers[] | .name'

# Get details for a specific server
curl https://registry.modelcontextprotocol.io/v0.1/servers/ai.smithery%2Fsmithery-ai-github/versions/latest | jq '.'
```

### Test Server Before Importing

Use the test script to verify server details:

```bash
./cli/test_anthropic_api.py ai.smithery/smithery-ai-github
```

## Verifying Imported Servers

### Check Server Status

After importing, verify the server was registered:

```bash
# Via CLI
./cli/service_mgmt.sh list

# Via API
curl http://localhost/mcpgw/mcp -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
```

### View Server in UI

Navigate to the gateway UI to see imported servers:
- http://localhost/

### Check Health Status

The health check service automatically monitors imported servers:

```bash
docker compose logs registry | grep -i "health"
```

## Troubleshooting

### Import Fails with Authentication Error

**Problem**: Server requires authentication but key is missing

**Solution**:
1. Check if the server requires an API key
2. Add the key to your `.env` file with the correct name
3. Re-run the import

### Server Shows as Unhealthy

**Problem**: Imported server shows unhealthy in health checks

**Possible causes**:
- Invalid or expired API key
- Network connectivity issues
- Server is temporarily down

**Check logs**:
```bash
docker compose logs registry --tail 100 | grep -i "server-name"
```

### Environment Variable Not Substituted

**Problem**: Server JSON still shows `${VAR_NAME}` instead of actual value

**Solution**:
1. Ensure the variable is defined in `.env`
2. Variable names are case-sensitive
3. Re-run the import after updating `.env`

### Server Name Conflicts

**Problem**: Server already exists with same path

**Solution**:
```bash
# Delete existing server
./cli/service_mgmt.sh delete /server-path "server-name"

# Re-import
./cli/import_from_anthropic_registry.sh server-name
```

## Advanced Usage

### Custom Transformation

To customize how servers are imported, edit `cli/anthropic_transformer.py`:

- Modify tag generation
- Change path formatting
- Adjust authentication handling
- Add custom metadata

### Batch Import with Filtering

```bash
# Import only servers matching a pattern
curl -s https://registry.modelcontextprotocol.io/v0.1/servers | \
  jq -r '.servers[] | select(.name | contains("smithery")) | .name' > smithery-servers.txt

./cli/import_from_anthropic_registry.sh --import-list smithery-servers.txt
```

### Automated Imports

Add to cron or systemd timer for automatic updates:

```bash
# Daily import of curated server list
0 2 * * * cd /path/to/repo && ./cli/import_from_anthropic_registry.sh --import-list cli/import_server_list.txt
```

## Best Practices

1. **Curate your server list**: Only import servers you need and trust
2. **Review before importing**: Use `--dry-run` to preview changes
3. **Secure API keys**: Never commit `.env` to version control
4. **Monitor health**: Regularly check imported server health status
5. **Update regularly**: Re-import servers to get latest configurations
6. **Test thoroughly**: Verify each server works after importing

## Related Documentation

- [Anthropic MCP Registry API](anthropic_registry_api.md)
- [Service Management](service-management.md)
- [Authentication Setup](../README.md#authentication)
- [Health Monitoring](OBSERVABILITY.md)

## Support

For issues or questions:
- GitHub Issues: [mcp-gateway-registry/issues](https://github.com/agentic-community/mcp-gateway-registry/issues)
- Anthropic Registry: [modelcontextprotocol.io](https://modelcontextprotocol.io/)
