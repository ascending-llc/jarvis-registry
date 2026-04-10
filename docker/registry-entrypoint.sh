#!/bin/bash
set -e

# Unified entrypoint for registry frontend and backend.
# Usage:
#   /entrypoint.sh frontend
#   /entrypoint.sh backend

MODE="${1}"

SSL_CERT_PATH="${SSL_CERT_PATH:-/etc/ssl/certs/fullchain.pem}"
SSL_KEY_PATH="${SSL_KEY_PATH:-/etc/ssl/private/privkey.pem}"

install_nginx_config() {
    HTTP_ONLY_TEMPLATE_PATH="${1:?}"
    HTTP_AND_HTTPS_TEMPLATE_PATH="${2:?}"
    DEST_PATH="${3:?}"

    echo "Checking for SSL certificates..."
    if [ ! -f "$SSL_CERT_PATH" ] || [ ! -f "$SSL_KEY_PATH" ]; then
        echo "=========================================="
        echo "SSL certificates not found - HTTPS will not be available"
        echo "=========================================="
        echo ""
        echo "To enable HTTPS, mount your certificates to:"
        echo "  - $SSL_CERT_PATH"
        echo "  - $SSL_KEY_PATH"
        echo ""
        echo "Example for docker-compose.yml:"
        echo "  volumes:"
        echo "    - /path/to/fullchain.pem:/etc/ssl/certs/fullchain.pem:ro"
        echo "    - /path/to/privkey.pem:/etc/ssl/private/privkey.pem:ro"
        echo ""
        echo "HTTP server will be available on port 80"
        echo "=========================================="
    else
        echo "=========================================="
        echo "SSL certificates found - HTTPS enabled"
        echo "=========================================="
        echo "Certificate: $SSL_CERT_PATH"
        echo "Private key: $SSL_KEY_PATH"
        echo "HTTPS server will be available on port 443"
        echo "=========================================="
    fi

    # Check if SSL certificates exist and use appropriate config
    if [ ! -f "$SSL_CERT_PATH" ] || [ ! -f "$SSL_KEY_PATH" ]; then
        echo "Using HTTP-only Nginx configuration (no SSL certificates)..."
        TEMPLATE_PATH="$HTTP_ONLY_TEMPLATE_PATH"
    else
        echo "Using HTTP + HTTPS Nginx configuration (SSL certificates found)..."
        TEMPLATE_PATH="$HTTP_AND_HTTPS_TEMPLATE_PATH"
    fi

    # Process template with envsubst and handle NGINX_BASE_PATH location directive
    if [ -z "$NGINX_BASE_PATH" ]; then
        # When NGINX_BASE_PATH is empty, location becomes "/" (with trailing slash)
        echo "NGINX_BASE_PATH is empty - using root path location /"
        envsubst '${NGINX_BASE_PATH}' <"$TEMPLATE_PATH" >"$DEST_PATH"
    else
        # When NGINX_BASE_PATH is not empty, remove trailing slash from location directive
        echo "NGINX_BASE_PATH is '${NGINX_BASE_PATH}' - adjusting location directive (no trailing slash)"
        envsubst '${NGINX_BASE_PATH}' <"$TEMPLATE_PATH" |
            sed "s|location ${NGINX_BASE_PATH}/ {|location ${NGINX_BASE_PATH} {|g" >"$DEST_PATH"
    fi

    echo "Nginx configuration installed."
}

case "$MODE" in
frontend)
    echo "Starting Registry Frontend Setup..."

    # NGINX_BASE_PATH defaults to empty string (root path /)
    export NGINX_BASE_PATH="${NGINX_BASE_PATH:-}"
    echo "NGINX_BASE_PATH configured as: '${NGINX_BASE_PATH:-/}'"

    # Generate runtime config.js for React app
    cat >/usr/share/nginx/html/config.js <<EOF
// Runtime configuration - generated at container startup
window.__RUNTIME_CONFIG__ = {
  BASE_PATH: "${NGINX_BASE_PATH}"
};
EOF
    echo "Generated config.js with BASE_PATH=${NGINX_BASE_PATH}"

    # Config paths matching Dockerfile.registry-frontend
    NGINX_HTTP_ONLY_CONF="/nginx_http_only.conf"
    NGINX_HTTP_AND_HTTPS_CONF="/nginx_http_and_https.conf"
    NGINX_CONFIG_PATH="/etc/nginx/conf.d/default.conf"

    install_nginx_config "$NGINX_HTTP_ONLY_CONF" "$NGINX_HTTP_AND_HTTPS_CONF" "$NGINX_CONFIG_PATH"

    echo "Starting Nginx..."
    nginx -g 'daemon off;'
    ;;
backend)
    echo "Starting MCP Registry Service..."

    if [ -n "${BUILD_VERSION}" ]; then
        echo "Using BUILD_VERSION from environment: $BUILD_VERSION"
    else
        echo "BUILD_VERSION not set, will use default version"
    fi

    echo "Running in ${TOOL_DISCOVERY_MODE:-unknown} tool discovery mode"

    # Start the registry
    echo "Starting MCP Registry on port 7860..."
    exec uvicorn registry.main:app --host 0.0.0.0 --port 7860
    ;;
*)
    echo "Unknown MODE: $MODE"
    echo "Valid modes: frontend, backend"
    exit 2
    ;;
esac
