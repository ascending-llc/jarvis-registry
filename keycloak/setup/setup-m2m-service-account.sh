#!/bin/bash
# Complete M2M Service Account Setup Script
# This script handles all aspects of setting up the M2M service account for Keycloak

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
SERVICE_ACCOUNT="service-account-mcp-gateway-m2m"
M2M_CLIENT="mcp-gateway-m2m"
TARGET_GROUP="registry-admin"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Setting up M2M Service Account for Keycloak${NC}"
echo "=============================================="
echo "Service Account: $SERVICE_ACCOUNT"
echo "Target Group: $TARGET_GROUP"
echo "M2M Client: $M2M_CLIENT"
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
        "serviceAccountClientId": "'$M2M_CLIENT'"
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

# Function to get M2M client ID
get_m2m_client_id() {
    echo "Finding M2M client..."
    CLIENT_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
        "$ADMIN_URL/admin/realms/$REALM/clients?clientId=$M2M_CLIENT" | \
        jq -r '.[0].id // empty')

    if [ -z "$CLIENT_ID" ] || [ "$CLIENT_ID" = "null" ]; then
        echo -e "${RED}M2M client '$M2M_CLIENT' not found${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ Found M2M client with ID: $CLIENT_ID${NC}"
}

# Function to add groups mapper to M2M client
add_groups_mapper() {
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

# Main execution
main() {
    get_admin_token

    # Step 1: Ensure service account exists
    if ! check_service_account; then
        create_service_account
    fi

    # Step 2: Ensure target group exists
    ensure_target_group

    # Step 3: Assign service account to group
    assign_to_group

    # Step 4: Get M2M client ID
    get_m2m_client_id

    # Step 5: Add groups mapper
    add_groups_mapper

    # Step 6: Verify everything is set up correctly
    verify_setup

    echo ""
    echo -e "${GREEN}SUCCESS! M2M service account setup complete.${NC}"
    echo ""
    echo -e "${YELLOW}Next steps:${NC}"
    echo "1. Generate a new M2M token to get the group membership:"
    echo "   python credentials-provider/token_refresher.py"
    echo ""
    echo "2. Test the authentication:"
    echo "   ./test-keycloak-mcp.sh"
    echo ""
    echo -e "${YELLOW}Summary:${NC}"
    echo "- Service Account: $SERVICE_ACCOUNT"
    echo "- Group: $TARGET_GROUP"
    echo "- Client: $M2M_CLIENT"
    echo "- Groups Mapper: ✓ Configured"
}

# Run main function
main
