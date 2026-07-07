#!/bin/bash
set -e

# Unified entrypoint for registry frontend and backend.
# Usage:
#   /entrypoint.sh frontend
#   /entrypoint.sh backend

MODE="${1}"

install_nginx_config() {
    TEMPLATE_PATH="${1:?}"
    DEST_PATH="${2:?}"

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

    if [ -z "$NGINX_BASE_PATH" ]; then
        FRONTEND_BASE_HREF="/"
    else
        FRONTEND_BASE_HREF="${NGINX_BASE_PATH%/}/"
    fi
    export FRONTEND_BASE_HREF
    echo "Frontend base href configured as: '${FRONTEND_BASE_HREF}'"

    # Generate runtime config.js for React app
    cat >/usr/share/nginx/html/config.js <<EOF
// Runtime configuration - generated at container startup
window.__RUNTIME_CONFIG__ = {
  BASE_PATH: "${NGINX_BASE_PATH}"
};
EOF
    echo "Generated config.js with BASE_PATH=${NGINX_BASE_PATH}"

    # Vite builds this image without knowing the runtime mount path. Its JS/CSS
    # assets are intentionally relative, so inject a base URL at startup to make
    # deep links like /gateway/consent/downstream resolve ./assets and config.js
    # under /gateway instead of /gateway/consent.
    sed -i '/<base href=/d' /usr/share/nginx/html/index.html
    sed -i "s|<head>|<head><base href=\"${FRONTEND_BASE_HREF}\">|" /usr/share/nginx/html/index.html

    # Config paths matching Dockerfile.registry-frontend
    NGINX_HTTP_ONLY_CONF="/nginx_http_only.conf"
    NGINX_CONFIG_PATH="/etc/nginx/conf.d/default.conf"

    install_nginx_config "$NGINX_HTTP_ONLY_CONF" "$NGINX_CONFIG_PATH"

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
    # Pass NGINX_BASE_PATH as uvicorn --root-path so that:
    # 1. scope["root_path"] is set correctly for Swagger UI URL generation.
    # 2. scope["path"] is prefixed so that get_route_path() strips it, which
    #    fixes routing for mounted sub-apps (e.g. FastMCP at /proxy/mcpgw).
    # Do NOT set root_path= on the FastAPI constructor - that overwrites scope["root_path"]
    # on every request and breaks sub-app mount matching.
    echo "Starting MCP Registry on port 7860..."
    exec uvicorn registry.main:app --host 0.0.0.0 --port 7860 --root-path "${NGINX_BASE_PATH:-}"
    ;;
*)
    echo "Unknown MODE: $MODE"
    echo "Valid modes: frontend, backend"
    exit 2
    ;;
esac
