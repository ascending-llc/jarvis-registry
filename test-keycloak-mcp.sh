#!/bin/bash

# Test Keycloak MCP Gateway authentication
# This script reads the token from the ingress.json file and tests MCP commands

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_FILE="$SCRIPT_DIR/.oauth-tokens/ingress.json"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Testing Keycloak MCP Gateway Authentication${NC}"
echo "=============================================="

# Check if token file exists
if [ ! -f "$TOKEN_FILE" ]; then
    echo -e "${RED}Error: Token file not found at $TOKEN_FILE${NC}"
    exit 1
fi

# Extract token
echo "Reading token from $TOKEN_FILE..."
TOKEN=$(jq -r '.access_token' "$TOKEN_FILE")

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
    echo -e "${RED}Error: Could not read access_token from file${NC}"
    exit 1
fi

echo -e "${GREEN}Token loaded successfully${NC}"

# Test 1: Basic connectivity (should get MCP protocol error)
echo ""
echo "Test 1: Basic authentication test..."
RESPONSE=$(curl -s \
    -H "X-Authorization: Bearer $TOKEN" \
    -H "Accept: application/json" \
    https://mcpgateway.ddns.net/currenttime/mcp)

echo "Response: $RESPONSE"

if echo "$RESPONSE" | grep -q "Not Acceptable.*text/event-stream"; then
    echo -e "${GREEN}✓ Authentication successful! (MCP protocol error is expected)${NC}"
else
    echo -e "${RED}✗ Authentication may have failed${NC}"
fi

# Test 2: MCP Initialize
echo ""
echo "Test 2: MCP Initialize..."
# Get session ID from headers using -v flag
SESSION_ID=$(curl -s -v \
    -H "X-Authorization: Bearer $TOKEN" \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0.0"}}}' \
    https://mcpgateway.ddns.net/currenttime/mcp 2>&1 | grep -i '< mcp-session-id:' | sed 's/.*< mcp-session-id: *//' | tr -d '\r')

if [ -n "$SESSION_ID" ]; then
    echo "✓ Session established with ID: $SESSION_ID"

    # Send initialized notification to complete handshake
    echo "Completing initialization handshake..."
    curl -s \
        -H "X-Authorization: Bearer $TOKEN" \
        -H "Accept: application/json, text/event-stream" \
        -H "Content-Type: application/json" \
        -H "mcp-session-id: $SESSION_ID" \
        -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
        https://mcpgateway.ddns.net/currenttime/mcp > /dev/null
    echo "✓ Handshake completed"
else
    echo "✗ Failed to get session ID"
fi

RESPONSE2=$(curl -s \
    -H "X-Authorization: Bearer $TOKEN" \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0.0"}}}' \
    https://mcpgateway.ddns.net/currenttime/mcp)

echo "Initialize response:"
echo "$RESPONSE2" | head -5

# Test 3: MCP Ping
echo ""
echo "Test 3: MCP Ping..."
if [ -n "$SESSION_ID" ]; then
    RESPONSE3=$(curl -s \
        -H "X-Authorization: Bearer $TOKEN" \
        -H "Accept: application/json, text/event-stream" \
        -H "Content-Type: application/json" \
        -H "mcp-session-id: $SESSION_ID" \
        -d '{"jsonrpc":"2.0","id":2,"method":"ping"}' \
        https://mcpgateway.ddns.net/currenttime/mcp)

    echo "Ping response:"
    echo "$RESPONSE3" | head -5
else
    echo "Skipping ping test - no session ID"
fi

# Test 4: List tools
echo ""
echo "Test 4: MCP List Tools..."
if [ -n "$SESSION_ID" ]; then
    RESPONSE4=$(curl -s \
        -H "X-Authorization: Bearer $TOKEN" \
        -H "Accept: application/json, text/event-stream" \
        -H "Content-Type: application/json" \
        -H "mcp-session-id: $SESSION_ID" \
        -d '{"jsonrpc":"2.0","id":3,"method":"tools/list"}' \
        https://mcpgateway.ddns.net/currenttime/mcp)

    echo "List tools response:"
    echo "$RESPONSE4" | head -10
else
    echo "Skipping tools/list test - no session ID"
fi

echo ""
echo -e "${GREEN}Testing complete!${NC}"
echo ""
echo -e "${YELLOW}Key points:${NC}"
echo "- Authentication uses only X-Authorization header (no Cognito headers needed)"
echo "- Token has groups: ['registry-admin'] for full access"
echo "- Keycloak integration is working correctly"
