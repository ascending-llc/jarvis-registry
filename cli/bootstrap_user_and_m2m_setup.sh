#!/bin/bash
# Bootstrap script for setting up LOB users and M2M service accounts
# # Creates registry-admin, register-user, and register-read-only groups
# Then creates bot and human users in these groups

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"
REGISTRY_MGMT_SCRIPT="$PROJECT_ROOT/api/registry_management.py"

# Load environment variables from .env file
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE"
    exit 1
fi

set -a
source "$ENV_FILE"
set +a

# Configuration - read from .env variables
ADMIN_URL="${KEYCLOAK_ADMIN_URL}"
REALM="${KEYCLOAK_REALM}"
ADMIN_USER="${KEYCLOAK_ADMIN}"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASSWORD}"
INITIAL_USER_PASSWORD="${INITIAL_USER_PASSWORD}"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'


_print_section() {
    echo ""
    echo -e "${BLUE}=============================================="
    echo "$1"
    echo "===============================================${NC}"
}


_print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}


_print_error() {
    echo -e "${RED}Error: $1${NC}"
}


_print_info() {
    echo -e "${YELLOW}$1${NC}"
}


_validate_environment() {
    local missing_vars=()

    if [ -z "$ADMIN_URL" ]; then
        missing_vars+=("KEYCLOAK_ADMIN_URL")
    fi

    if [ -z "$REALM" ]; then
        missing_vars+=("KEYCLOAK_REALM")
    fi

    if [ -z "$ADMIN_USER" ]; then
        missing_vars+=("KEYCLOAK_ADMIN")
    fi

    if [ -z "$ADMIN_PASS" ]; then
        missing_vars+=("KEYCLOAK_ADMIN_PASSWORD")
    fi

    if [ -z "$INITIAL_USER_PASSWORD" ]; then
        missing_vars+=("INITIAL_USER_PASSWORD")
    fi

    if [ ${#missing_vars[@]} -gt 0 ]; then
        _print_error "Missing required environment variables in .env file:"
        for var in "${missing_vars[@]}"; do
            echo "  - $var"
        done
        echo ""
        echo "Please update $ENV_FILE with the missing values"
        exit 1
    fi
}


_get_admin_token() {
    TOKEN=$(curl -s -X POST "$ADMIN_URL/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=$ADMIN_USER" \
        -d "password=$ADMIN_PASS" \
        -d "grant_type=password" \
        -d "client_id=admin-cli" | jq -r '.access_token // empty')

    if [ -z "$TOKEN" ]; then
        _print_error "Failed to get admin token"
        exit 1
    fi
}


_create_group() {
    local group_name="$1"

    echo "Creating group: $group_name"

    # Check if group already exists
    EXISTING_GROUP=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/groups" | \
        jq -r ".[] | select(.name==\"$group_name\") | .id")

    if [ -n "$EXISTING_GROUP" ] && [ "$EXISTING_GROUP" != "null" ]; then
        _print_info "Group '$group_name' already exists (ID: $EXISTING_GROUP)"
        return 0
    fi

    # Create the group
    GROUP_JSON="{
        \"name\": \"$group_name\"
    }"

    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$ADMIN_URL/admin/realms/$REALM/groups" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$GROUP_JSON")

    if [ "$RESPONSE" = "201" ]; then
        _print_success "Created group: $group_name"
    else
        _print_error "Failed to create group '$group_name'. HTTP: $RESPONSE"
        exit 1
    fi
}


_check_registry_mgmt_script() {
    if [ ! -f "$REGISTRY_MGMT_SCRIPT" ]; then
        _print_error "registry_management.py not found at $REGISTRY_MGMT_SCRIPT"
        exit 1
    fi

    if [ ! -x "$REGISTRY_MGMT_SCRIPT" ]; then
        chmod +x "$REGISTRY_MGMT_SCRIPT"
    fi

    _print_success "registry_management.py found and is executable"
}


_create_lob1_users() {
    _print_section "Creating LOB1 Bot and Human Users"

    echo "Creating M2M service account: lob1-bot"
    if uv run python "$REGISTRY_MGMT_SCRIPT" user-create-m2m \
        --name "lob1-bot" \
        --groups "register-user" \
        --description "M2M service account for LOB1" 2>&1 | tee /tmp/lob1_bot_output.txt; then
        _print_success "Created lob1-bot"
    else
        if grep -q "already exists" /tmp/lob1_bot_output.txt; then
            _print_info "lob1-bot already exists, continuing..."
        else
            _print_error "Failed to create lob1-bot"
            exit 1
        fi
    fi

    echo ""
    echo "Creating human user: lob1-user"
    if uv run python "$REGISTRY_MGMT_SCRIPT" user-create-human \
        --username "lob1-user" \
        --email "lob1-user@example.com" \
        --first-name "LOB1" \
        --last-name "User" \
        --groups "register-user" \
        --password "$INITIAL_USER_PASSWORD" 2>&1 | tee /tmp/lob1_user_output.txt; then
        _print_success "Created lob1-user"
    else
        if grep -q "already exists" /tmp/lob1_user_output.txt; then
            _print_info "lob1-user already exists, continuing..."
        else
            _print_error "Failed to create lob1-user"
            exit 1
        fi
    fi
}


_create_lob2_users() {
    _print_section "Creating LOB2 Bot and Human Users"

    echo "Creating M2M service account: lob2-bot"
    if uv run python "$REGISTRY_MGMT_SCRIPT" user-create-m2m \
        --name "lob2-bot" \
        --groups "register-read-only" \
        --description "M2M service account for LOB2" 2>&1 | tee /tmp/lob2_bot_output.txt; then
        _print_success "Created lob2-bot"
    else
        if grep -q "already exists" /tmp/lob2_bot_output.txt; then
            _print_info "lob2-bot already exists, continuing..."
        else
            _print_error "Failed to create lob2-bot"
            exit 1
        fi
    fi

    echo ""
    echo "Creating human user: lob2-user"
    if uv run python "$REGISTRY_MGMT_SCRIPT" user-create-human \
        --username "lob2-user" \
        --email "lob2-user@example.com" \
        --first-name "LOB2" \
        --last-name "User" \
        --groups "register-read-only" \
        --password "$INITIAL_USER_PASSWORD" 2>&1 | tee /tmp/lob2_user_output.txt; then
        _print_success "Created lob2-user"
    else
        if grep -q "already exists" /tmp/lob2_user_output.txt; then
            _print_info "lob2-user already exists, continuing..."
        else
            _print_error "Failed to create lob2-user"
            exit 1
        fi
    fi
}


_create_admin_users() {
    _print_section "Creating Admin Bot and Admin User"

    echo "Creating M2M service account: admin-bot"
    if uv run python "$REGISTRY_MGMT_SCRIPT" user-create-m2m \
        --name "admin-bot" \
        --groups "registry-admin" \
        --description "M2M service account for admin operations" 2>&1 | tee /tmp/admin_bot_output.txt; then
        _print_success "Created admin-bot"
    else
        if grep -q "already exists" /tmp/admin_bot_output.txt; then
            _print_info "admin-bot already exists, continuing..."
        else
            _print_error "Failed to create admin-bot"
            exit 1
        fi
    fi

    echo ""
    echo "Creating human user: admin-user"
    if uv run python "$REGISTRY_MGMT_SCRIPT" user-create-human \
        --username "admin-user" \
        --email "admin-user@example.com" \
        --first-name "Admin" \
        --last-name "User" \
        --groups "registry-admin" \
        --password "$INITIAL_USER_PASSWORD" 2>&1 | tee /tmp/admin_user_output.txt; then
        _print_success "Created admin-user"
    else
        if grep -q "already exists" /tmp/admin_user_output.txt; then
            _print_info "admin-user already exists, continuing..."
        else
            _print_error "Failed to create admin-user"
            exit 1
        fi
    fi
}


_assign_mcp_gateway_to_registry_admins() {
    _print_section "Assigning MCP Gateway Service Account to registry-admin"

    local service_account_name="service-account-mcp-gateway-m2m"

    echo "Looking up service account: $service_account_name"
    local service_account_id=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/users?username=$service_account_name" | \
        jq -r '.[0].id // empty')

    if [ -z "$service_account_id" ]; then
        _print_info "Service account '$service_account_name' not found in Keycloak. This may be expected if using external M2M setup."
        return 0
    fi

    echo "Found service account with ID: $service_account_id"

    echo "Looking up registry-admin group"
    local registry_admins_group_id=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/groups" | \
        jq -r '.[] | select(.name=="registry-admin") | .id')

    if [ -z "$registry_admins_group_id" ] || [ "$registry_admins_group_id" = "null" ]; then
        _print_error "Could not find registry-admin group"
        return 1
    fi

    echo "Found registry-admin group with ID: $registry_admins_group_id"

    echo "Assigning service account to registry-admin group"
    local assign_response=$(curl -s -o /dev/null -w "%{http_code}" \
        -X PUT "$ADMIN_URL/admin/realms/$REALM/users/$service_account_id/groups/$registry_admins_group_id" \
        -H "Authorization: Bearer $TOKEN")

    if [ "$assign_response" = "204" ]; then
        _print_success "Service account assigned to registry-admin group"
    else
        _print_error "Failed to assign service account to registry-admin group (HTTP $assign_response)"
        return 1
    fi
}


_print_summary() {
    _print_section "Bootstrap Setup Complete"

    echo ""
    _print_info "Created Groups:"
    echo "  - register-user"
    echo "  - register-read-only"
    echo "  - registry-admin"

    echo ""
    _print_info "Created LOB1 Users:"
    echo "  - Bot: lob1-bot (M2M service account)"
    echo "  - Human: lob1-user (password: INITIAL_USER_PASSWORD env var)"

    echo ""
    _print_info "Created LOB2 Users:"
    echo "  - Bot: lob2-bot (M2M service account)"
    echo "  - Human: lob2-user (password: INITIAL_USER_PASSWORD env var)"

    echo ""
    _print_info "Created Admin Users:"
    echo "  - Bot: admin-bot (M2M service account)"
    echo "  - Human: admin-user (password: INITIAL_USER_PASSWORD env var)"

    echo ""
    _print_info "Next Steps:"
    echo "  1. Update scopes.yml to configure access for these groups"
    echo "  2. Regenerate admin-bot token using: ./keycloak/setup/generate-agent-token.sh admin-bot"
    echo "  3. Test access with the generated tokens"
    echo "  4. Login to dashboard as admin-user, lob1-user, or lob2-user"

    echo ""
    _print_info "Credentials saved to: .oauth-tokens/"
}


main() {
    _print_section "Bootstrap: LOB User and M2M Setup"

    # Validate environment variables
    _validate_environment

    # Check if registry_management.py exists
    _check_registry_mgmt_script

    # Get admin token
    echo "Authenticating with Keycloak..."
    _get_admin_token
    _print_success "Authentication successful"

    # Create groups
    _print_section "Creating Keycloak Groups"
    _create_group "register-user"
    _create_group "register-read-only"
    _create_group "registry-admin"

    # Create LOB1 users
    _create_lob1_users

    # Create LOB2 users
    _create_lob2_users

    # Create Admin users
    _create_admin_users

    # Assign MCP Gateway service account to registry-admin group
    _assign_mcp_gateway_to_registry_admins

    # Print summary
    _print_summary
}


main "$@"
