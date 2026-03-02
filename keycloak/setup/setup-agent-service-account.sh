#!/bin/bash
# Agent-Specific M2M Service Account Setup Script
# This script creates individual service accounts for AI agents with proper audit trails

set -e

# Configuration
ADMIN_URL="http://localhost:8080"
REALM="mcp-gateway"
ADMIN_USER="admin"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASSWORD}"

# Check required environment variables
if [ -z "$ADMIN_PASS" ]; then
    echo -e "${RED}Error: KEYCLOAK_ADMIN_PASSWORD environment variable is required${NC}"
    echo "Please set it before running this script:"
    echo "export KEYCLOAK_ADMIN_PASSWORD=\"your-secure-password\""
    exit 1
fi
M2M_CLIENT="mcp-gateway-m2m"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Usage function
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Create a Keycloak service account for an AI agent with proper audit trails"
    echo ""
    echo "Options:"
    echo "  -a, --agent-id AGENT_ID     Agent identifier (required)"
    echo "  -g, --group GROUP           Group assignment (default: register-read-only)"
    echo "  -c, --client CLIENT         M2M client name (default: mcp-gateway-m2m)"
    echo "  -h, --help                  Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --agent-id claude-001"
    echo "  $0 --agent-id bedrock-claude --group registry-admin"
    echo "  $0 -a gpt4-turbo -g register-read-only"
    echo "  $0 -a finance-agent -g register-user"
    echo ""
    echo "Service Account Naming: agent-{agent-id}-m2m"
    echo ""
    echo "Common Groups:"
    echo "  - register-read-only         (limited access)"
    echo "  - register-user              (create servers and agents)"
    echo "  - registry-power-user        (create/delete/share servers)"
    echo "  - registry-admin             (full access)"
    echo ""
    echo "Note: Group must exist in Keycloak. Script will validate and show available groups if invalid."
}

# Parse command line arguments
AGENT_ID=""
TARGET_GROUP="register-read-only"

while [[ $# -gt 0 ]]; do
    case $1 in
        -a|--agent-id)
            AGENT_ID="$2"
            shift 2
            ;;
        -g|--group)
            TARGET_GROUP="$2"
            shift 2
            ;;
        -c|--client)
            M2M_CLIENT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# Validate required parameters
if [ -z "$AGENT_ID" ]; then
    echo -e "${RED}Error: Agent ID is required${NC}"
    usage
    exit 1
fi

# Generate service account name and client ID
SERVICE_ACCOUNT="agent-${AGENT_ID}-m2m"
AGENT_CLIENT_ID="agent-${AGENT_ID}-m2m"

echo -e "${BLUE}Setting up Agent-Specific M2M Client and Service Account${NC}"
echo "=============================================="
echo "Agent ID: $AGENT_ID"
echo "Agent Client ID: $AGENT_CLIENT_ID"
echo "Service Account: $SERVICE_ACCOUNT"
echo "Target Group: $TARGET_GROUP"
echo ""

# Function to get admin token
get_admin_token() {
    echo "Getting admin token..."
    TOKEN=$(curl -s -X POST "$ADMIN_URL/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=$ADMIN_USER" \
        -d "password=$ADMIN_PASS" \
        -d "grant_type=password" \
        -d "client_id=admin-cli" | jq -r '.access_token // empty')

    if [ -z "$TOKEN" ]; then
        echo -e "${RED}Failed to get admin token${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Admin token obtained${NC}"
}

# Function to validate group exists
validate_group_exists() {
    echo "Validating group exists: $TARGET_GROUP..."

    GROUP_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/groups" | \
        jq -r ".[] | select(.name==\"$TARGET_GROUP\") | .id")

    if [ -z "$GROUP_ID" ] || [ "$GROUP_ID" = "null" ]; then
        echo -e "${RED}Error: Group '$TARGET_GROUP' does not exist in Keycloak${NC}"
        echo -e "${YELLOW}Available groups:${NC}"
        curl -s -H "Authorization: Bearer $TOKEN" \
            "$ADMIN_URL/admin/realms/$REALM/groups" | \
            jq -r '.[].name' | sed 's/^/  - /'
        exit 1
    fi

    echo -e "${GREEN}✓ Group '$TARGET_GROUP' exists${NC}"
}

# Function to create agent-specific M2M client
create_agent_m2m_client() {
    echo "Creating agent-specific M2M client..."

    # Check if client already exists
    EXISTING_CLIENT=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/clients?clientId=$AGENT_CLIENT_ID" | \
        jq -r '.[0].id // empty')

    if [ ! -z "$EXISTING_CLIENT" ] && [ "$EXISTING_CLIENT" != "null" ]; then
        echo -e "${YELLOW}Agent M2M client already exists with ID: $EXISTING_CLIENT${NC}"
        CLIENT_ID="$EXISTING_CLIENT"
        return 0
    fi

    # Create the M2M client
    CLIENT_JSON='{
        "clientId": "'$AGENT_CLIENT_ID'",
        "name": "Agent M2M Client for '$AGENT_ID'",
        "description": "Machine-to-Machine client for AI agent '$AGENT_ID' with individual audit trails",
        "enabled": true,
        "clientAuthenticatorType": "client-secret",
        "serviceAccountsEnabled": true,
        "standardFlowEnabled": false,
        "implicitFlowEnabled": false,
        "directAccessGrantsEnabled": false,
        "publicClient": false,
        "protocol": "openid-connect",
        "attributes": {
            "agent_id": "'$AGENT_ID'",
            "client_type": "agent_m2m",
            "created_by": "keycloak_setup_script"
        },
        "defaultClientScopes": [
            "web-origins",
            "acr",
            "profile",
            "roles",
            "email"
        ]
    }'

    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$ADMIN_URL/admin/realms/$REALM/clients" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$CLIENT_JSON")

    if [ "$RESPONSE" = "201" ]; then
        echo -e "${GREEN}✓ Agent M2M client created successfully${NC}"

        # Get the client ID
        CLIENT_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
            "$ADMIN_URL/admin/realms/$REALM/clients?clientId=$AGENT_CLIENT_ID" | \
            jq -r '.[0].id')

        echo "Client UUID: $CLIENT_ID"
    else
        echo -e "${RED}Failed to create agent M2M client. HTTP: $RESPONSE${NC}"
        exit 1
    fi
}

# Function to check if service account user exists
check_service_account() {
    echo "Checking if service account exists..."
    USER_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/users?username=$SERVICE_ACCOUNT" | \
        jq -r '.[0].id // empty')

    if [ -n "$USER_ID" ] && [ "$USER_ID" != "null" ]; then
        echo -e "${GREEN}✓ Service account already exists with ID: $USER_ID${NC}"
        return 0
    else
        echo "Service account does not exist"
        return 1
    fi
}

# Function to create service account user
create_service_account() {
    echo "Creating service account user..."

    USER_JSON='{
        "username": "'$SERVICE_ACCOUNT'",
        "enabled": true,
        "emailVerified": true,
        "serviceAccountClientId": "'$AGENT_CLIENT_ID'",
        "attributes": {
            "agent_id": ["'$AGENT_ID'"],
            "agent_client_id": ["'$AGENT_CLIENT_ID'"],
            "account_type": ["agent_service_account"],
            "created_by": ["keycloak_setup_script"]
        }
    }'

    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$ADMIN_URL/admin/realms/$REALM/users" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$USER_JSON")

    if [ "$RESPONSE" = "201" ]; then
        echo -e "${GREEN}✓ Service account user created successfully${NC}"

        # Get the user ID
        USER_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
            "$ADMIN_URL/admin/realms/$REALM/users?username=$SERVICE_ACCOUNT" | \
            jq -r '.[0].id')

        echo "User ID: $USER_ID"
    else
        echo -e "${RED}Failed to create user. HTTP: $RESPONSE${NC}"
        exit 1
    fi
}

# Function to get or create target group
ensure_target_group() {
    echo "Checking if target group exists..."
    GROUP_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/groups" | \
        jq -r ".[] | select(.name==\"$TARGET_GROUP\") | .id")

    if [ -n "$GROUP_ID" ] && [ "$GROUP_ID" != "null" ]; then
        echo -e "${GREEN}✓ Target group '$TARGET_GROUP' exists with ID: $GROUP_ID${NC}"
    else
        echo "Creating target group '$TARGET_GROUP'..."

        GROUP_JSON='{
            "name": "'$TARGET_GROUP'",
            "path": "/'$TARGET_GROUP'"
        }'

        RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST "$ADMIN_URL/admin/realms/$REALM/groups" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "$GROUP_JSON")

        if [ "$RESPONSE" = "201" ]; then
            echo -e "${GREEN}✓ Target group created successfully${NC}"

            # Get the group ID
            GROUP_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
                "$ADMIN_URL/admin/realms/$REALM/groups" | \
                jq -r ".[] | select(.name==\"$TARGET_GROUP\") | .id")

            echo "Group ID: $GROUP_ID"
        else
            echo -e "${RED}Failed to create group. HTTP: $RESPONSE${NC}"
            exit 1
        fi
    fi
}

# Function to assign service account to group
assign_to_group() {
    echo "Assigning service account to target group..."

    # Check if already assigned
    CURRENT_GROUPS=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/users/$USER_ID/groups" | \
        jq -r ".[].name")

    if echo "$CURRENT_GROUPS" | grep -q "$TARGET_GROUP"; then
        echo -e "${GREEN}✓ Service account already assigned to '$TARGET_GROUP' group${NC}"
        return 0
    fi

    # Assign to group
    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X PUT "$ADMIN_URL/admin/realms/$REALM/users/$USER_ID/groups/$GROUP_ID" \
        -H "Authorization: Bearer $TOKEN")

    if [ "$RESPONSE" = "204" ]; then
        echo -e "${GREEN}✓ Service account assigned to '$TARGET_GROUP' group${NC}"
    else
        echo -e "${RED}Failed to assign to group. HTTP: $RESPONSE${NC}"
        exit 1
    fi
}

# Function to get agent M2M client secret
get_agent_client_secret() {
    echo "Retrieving agent M2M client secret..."

    if [ -z "$CLIENT_ID" ]; then
        echo -e "${RED}Error: CLIENT_ID not set${NC}"
        exit 1
    fi

    # Get the client secret
    SECRET_RESPONSE=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/clients/$CLIENT_ID/client-secret")

    AGENT_CLIENT_SECRET=$(echo "$SECRET_RESPONSE" | jq -r '.value // empty')

    if [ -z "$AGENT_CLIENT_SECRET" ]; then
        echo -e "${RED}Failed to retrieve agent client secret${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ Agent client secret retrieved${NC}"
}

# Function to ensure groups mapper exists
ensure_groups_mapper() {
    echo "Checking for groups mapper on M2M client..."

    # Check if groups mapper already exists
    EXISTING_MAPPER=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/clients/$CLIENT_ID/protocol-mappers/models" | \
        jq -r '.[] | select(.name=="groups") | .id')

    if [ -n "$EXISTING_MAPPER" ] && [ "$EXISTING_MAPPER" != "null" ]; then
        echo -e "${GREEN}✓ Groups mapper already exists${NC}"
        return 0
    fi

    echo "Adding groups mapper to M2M client..."

    GROUPS_MAPPER='{
        "name": "groups",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-group-membership-mapper",
        "consentRequired": false,
        "config": {
            "full.path": "false",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "claim.name": "groups",
            "userinfo.token.claim": "true"
        }
    }'

    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$ADMIN_URL/admin/realms/$REALM/clients/$CLIENT_ID/protocol-mappers/models" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$GROUPS_MAPPER")

    if [ "$RESPONSE" = "201" ]; then
        echo -e "${GREEN}✓ Groups mapper added successfully${NC}"
    elif [ "$RESPONSE" = "409" ]; then
        echo -e "${GREEN}✓ Groups mapper already exists${NC}"
    else
        echo -e "${RED}Failed to add groups mapper. HTTP: $RESPONSE${NC}"
        exit 1
    fi
}

# Function to verify setup
verify_setup() {
    echo ""
    echo "Verifying setup..."

    # Check service account exists and is in the right group
    GROUPS=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/users/$USER_ID/groups" | \
        jq -r '.[].name')

    echo "Service account groups: $GROUPS"

    if echo "$GROUPS" | grep -q "$TARGET_GROUP"; then
        echo -e "${GREEN}✓ Service account is in '$TARGET_GROUP' group${NC}"
    else
        echo -e "${RED}✗ Service account is NOT in '$TARGET_GROUP' group${NC}"
        exit 1
    fi

    # Check groups mapper exists
    MAPPER_EXISTS=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/clients/$CLIENT_ID/protocol-mappers/models" | \
        jq -r '.[] | select(.name=="groups") | .name')

    if [ "$MAPPER_EXISTS" = "groups" ]; then
        echo -e "${GREEN}✓ Groups mapper is configured${NC}"
    else
        echo -e "${RED}✗ Groups mapper is NOT configured${NC}"
        exit 1
    fi
}

# Function to generate agent-specific token
generate_agent_token() {
    echo ""
    echo "Generating agent-specific token configuration..."

    # Create agent-specific token file
    AGENT_TOKEN_DIR=".oauth-tokens"
    AGENT_TOKEN_FILE="$AGENT_TOKEN_DIR/agent-${AGENT_ID}.json"

    mkdir -p "$AGENT_TOKEN_DIR"

    cat > "$AGENT_TOKEN_FILE" << EOF
{
  "provider": "keycloak_m2m",
  "agent_id": "$AGENT_ID",
  "service_account": "$SERVICE_ACCOUNT",
  "group": "$TARGET_GROUP",
  "client_id": "$AGENT_CLIENT_ID",
  "client_secret": "$AGENT_CLIENT_SECRET",
  "keycloak_url": "https://mcpgateway.ddns.net/keycloak",
  "realm": "$REALM",
  "saved_at": "$(date -u '+%Y-%m-%d %H:%M:%S UTC')",
  "usage_notes": "Individual M2M client credentials for agent $AGENT_ID with complete audit trails"
}
EOF

    echo -e "${GREEN}✓ Agent token configuration created: $AGENT_TOKEN_FILE${NC}"
}

# Main execution
main() {
    get_admin_token

    # Step 0: Validate group exists in Keycloak
    validate_group_exists

    # Step 1: Create agent-specific M2M client
    create_agent_m2m_client

    # Step 2: Get agent client secret
    get_agent_client_secret

    # Step 3: Create service account linked to agent client
    if ! check_service_account; then
        create_service_account
    fi

    # Step 4: Ensure target group exists
    ensure_target_group

    # Step 5: Assign service account to group
    assign_to_group

    # Step 6: Ensure groups mapper exists on agent client
    ensure_groups_mapper

    # Step 7: Verify everything is set up correctly
    verify_setup

    # Step 8: Generate agent-specific token configuration
    generate_agent_token

    echo ""
    echo -e "${GREEN}SUCCESS! Agent service account setup complete.${NC}"
    echo ""
    echo -e "${YELLOW}Agent Details:${NC}"
    echo "- Agent ID: $AGENT_ID"
    echo "- Agent Client ID: $AGENT_CLIENT_ID"
    echo "- Agent Client Secret: ${AGENT_CLIENT_SECRET:0:10}..."
    echo "- Service Account: $SERVICE_ACCOUNT"
    echo "- Group: $TARGET_GROUP"
    echo "- Token Config: .oauth-tokens/agent-${AGENT_ID}.json"
    echo ""
    echo -e "${YELLOW}Next steps:${NC}"
    echo "1. Generate agent-specific M2M token:"
    echo "   cd keycloak/setup && ./generate-agent-token.sh --agent-id $AGENT_ID --save"
    echo ""
    echo "2. Test the authentication:"
    echo "   ./test-keycloak-mcp.sh --agent-id $AGENT_ID"
    echo ""
    echo -e "${BLUE}Audit Trail Features:${NC}"
    echo "- All actions by this agent will be logged with agent ID: $AGENT_ID"
    echo "- Individual M2M client: $AGENT_CLIENT_ID"
    echo "- Service account username: $SERVICE_ACCOUNT"
    echo "- Group-based authorization: $TARGET_GROUP"
}

# Run main function
main
