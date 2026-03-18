#!/bin/bash
#
# Import MCP servers from Anthropic Registry
#
# This script fetches server definitions from the Anthropic MCP Registry
# and registers them with the local MCP Gateway Registry.
#
# Usage:
#   ./import_from_anthropic_registry.sh [--dry-run] [--import-list <file>] [--analyzers <analyzers>]
#
# Environment Variables:
#   GATEWAY_URL - Gateway URL (default: http://localhost)
#                 Example: export GATEWAY_URL=https://mcpgateway.ddns.net
#   MCP_SCANNER_LLM_API_KEY - API key for LLM-based security analysis (required if using llm analyzer)
#

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load environment variables from .env file if it exists
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a  # Automatically export all variables
    source "$PROJECT_ROOT/.env"
    set +a  # Turn off automatic export
fi

# Configuration
ANTHROPIC_API_BASE="https://registry.modelcontextprotocol.io"
TEMP_DIR="$PROJECT_ROOT/.tmp/anthropic-import"
BASE_PORT=8100

# Read API version from registry settings
ANTHROPIC_API_VERSION=$(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from registry.core.config import settings
print(settings.anthropic_api_version)
")

# Gateway URL (can be overridden with GATEWAY_URL environment variable)
GATEWAY_URL="${GATEWAY_URL:-http://localhost}"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

# Output formatting functions (minimal emoji use per coding standards)
print_success() { echo -e "${GREEN}[SUCCESS] $1${NC}"; }
print_error() { echo -e "${RED}[ERROR] $1${NC}"; }
print_info() { echo -e "${BLUE}[INFO] $1${NC}"; }

# Generate deployment instructions for a server
detect_transport() {
    local anthropic_json="$1"
    # Most MCP servers from Anthropic registry use stdio transport
    # Only a few support HTTP/SSE
    echo "stdio"
}

validate_package() {
    local package_type="$1"
    local package_name="$2"

    if [ -z "$package_name" ] || [ "$package_name" = "null" ]; then
        return 1
    fi

    case "$package_type" in
        "npm")
            # Check if NPM package exists (simplified check)
            return 0
            ;;
        "pypi")
            # Check if PyPI package exists (simplified check)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# Parse arguments
DRY_RUN=false
IMPORT_LIST="$SCRIPT_DIR/import_server_list.txt"
ANALYZERS="yara"

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) DRY_RUN=true; shift ;;
        --import-list) IMPORT_LIST="$2"; shift 2 ;;
        --analyzers) ANALYZERS="$2"; shift 2 ;;
        --help)
            echo "Usage: $0 [--dry-run] [--import-list <file>] [--analyzers <analyzers>]"
            echo ""
            echo "Options:"
            echo "  --dry-run              Dry run mode (don't register servers)"
            echo "  --import-list <file>   Server list file (default: import_server_list.txt)"
            echo "  --analyzers <list>     Security analyzers: yara, llm, or yara,llm (default: yara)"
            echo ""
            echo "Environment Variables:"
            echo "  GATEWAY_URL - Gateway URL (default: http://localhost)"
            echo "                Example: export GATEWAY_URL=https://mcpgateway.ddns.net"
            echo "  MCP_SCANNER_LLM_API_KEY - API key for LLM analyzer (required if using llm)"
            echo ""
            echo "Examples:"
            echo "  # Import with default YARA analyzer"
            echo "  $0"
            echo ""
            echo "  # Import with both YARA and LLM analyzers"
            echo "  export MCP_SCANNER_LLM_API_KEY=sk-..."
            echo "  $0 --analyzers yara,llm"
            echo ""
            echo "  # Import with only LLM analyzer"
            echo "  export MCP_SCANNER_LLM_API_KEY=sk-..."
            echo "  $0 --analyzers llm"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Check prerequisites
command -v jq >/dev/null || { print_error "jq required"; exit 1; }
command -v curl >/dev/null || { print_error "curl required"; exit 1; }
[ -f "$IMPORT_LIST" ] || { print_error "Import list not found: $IMPORT_LIST"; exit 1; }

# Check if LLM analyzer is requested and API key is available
if [[ "$ANALYZERS" == *"llm"* ]]; then
    if [ -z "$MCP_SCANNER_LLM_API_KEY" ] || [[ "$MCP_SCANNER_LLM_API_KEY" == *"your_"* ]] || [[ "$MCP_SCANNER_LLM_API_KEY" == *"placeholder"* ]]; then
        echo ""
        print_error "LLM analyzer requested but MCP_SCANNER_LLM_API_KEY is not configured"
        print_info "Current value: ${MCP_SCANNER_LLM_API_KEY:-<not set>}"
        print_info ""
        print_info "Options:"
        print_info "  1. Add real API key to .env file: MCP_SCANNER_LLM_API_KEY=sk-..."
        print_info "  2. Set environment variable: export MCP_SCANNER_LLM_API_KEY=sk-..."
        print_info "  3. Use only YARA analyzer: $0 --analyzers yara"
        exit 1
    fi
fi

mkdir -p "$TEMP_DIR"

# Read server list
servers=()
while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    servers+=("$(echo "$line" | xargs)")
done < "$IMPORT_LIST"

print_info "Found ${#servers[@]} servers to import"
print_info "Security analyzers: $ANALYZERS"

# Process each server
success_count=0
current_port=$BASE_PORT

for server_name in "${servers[@]}"; do
    print_info "Processing: $server_name"

    # Fetch from Anthropic API (URL encode server name)
    # API version is read from registry Settings
    encoded_name=$(echo "$server_name" | sed 's|/|%2F|g')
    api_url="${ANTHROPIC_API_BASE}/${ANTHROPIC_API_VERSION}/servers/${encoded_name}/versions/latest"
    safe_name=$(echo "$server_name" | sed 's|/|-|g')
    anthropic_file="${TEMP_DIR}/${safe_name}-anthropic.json"

    if ! curl -s -f "$api_url" > "$anthropic_file"; then
    print_error "Failed to fetch $server_name"
    continue
    fi

    # Transform to registry format
    config_file="${TEMP_DIR}/${safe_name}-config.json"
    anthropic_json=$(cat "$anthropic_file")

    # Extract from nested server object
    description=$(echo "$anthropic_json" | jq -r '.server.description // "Imported from Anthropic MCP Registry"')
    version=$(echo "$anthropic_json" | jq -r '.server.version // "latest"')
    repo_url=$(echo "$anthropic_json" | jq -r '.server.repository.url // ""')

    # Detect transport type from packages or remotes
    transport_type="stdio"
    if echo "$anthropic_json" | jq -e '.server.packages[]? | .transport.type' > /dev/null 2>&1; then
        transport_type=$(echo "$anthropic_json" | jq -r '.server.packages[]? | .transport.type' | head -1)
    elif echo "$anthropic_json" | jq -e '.server.remotes[]? | .type' > /dev/null 2>&1; then
        transport_type=$(echo "$anthropic_json" | jq -r '.server.remotes[]? | .type' | head -1)
    fi

    # Detect if Python
    is_python="false"
    if echo "$anthropic_json" | jq -e '.server.packages[]? | select(.registryType == "pypi")' > /dev/null 2>&1; then
        is_python="true"
    fi

    # Generate tags from server name
    IFS='/' read -ra name_parts <<< "$server_name"
    server_basename="${name_parts[${#name_parts[@]}-1]}"
    IFS='-' read -ra tag_parts <<< "$server_basename"
    tags_json=$(printf '%s\n' "${tag_parts[@]}" "anthropic-registry" | jq -R . | jq -s .)

    # Generate safe path and proxy URL
    safe_path=$(echo "$server_name" | sed 's|/|-|g')

    # For imported servers, use a placeholder URL since they're not deployed yet
        proxy_url="http://localhost:${current_port}/"

    # Use Python transformer for complete transformation
    python3 -c "
import json
import sys

sys.path.append('$SCRIPT_DIR')
from anthropic_transformer import transform_anthropic_to_gateway

# Load Anthropic server data
with open('$anthropic_file') as f:
    data = json.load(f)

# Transform to Gateway Registry format
result = transform_anthropic_to_gateway(data, $current_port)
result['path'] = '/$safe_path'

# Remove unsupported fields for register_service tool
# The user-facing register_service tool only supports basic fields
# Note: auth_type, auth_provider, headers, supported_transports, and tool_list are kept
unsupported_fields = [
    'repository_url', 'website_url', 'package_npm', 'remote_url'
]
for field in unsupported_fields:
    result.pop(field, None)

# Write transformed configuration
with open('$config_file', 'w') as f:
    json.dump(result, f, indent=2)
"

    print_success "Created config for $server_name (transport: $transport_type)"

    # Register with service_mgmt.sh (if not dry run)
    if [ "$DRY_RUN" = false ]; then
        if GATEWAY_URL="$GATEWAY_URL" "$SCRIPT_DIR/service_mgmt.sh" add "$config_file" "$ANALYZERS"; then
            print_success "Registered $server_name"
            success_count=$((success_count + 1))
        else
            print_error "Failed to register $server_name"
        fi
    else
        print_info "[DRY RUN] Would register $server_name with analyzers: $ANALYZERS"
        success_count=$((success_count + 1))
    fi

    current_port=$((current_port + 1))
done


print_info "Import completed: $success_count/${#servers[@]} successful"
print_info "Configuration files saved to: $TEMP_DIR"
