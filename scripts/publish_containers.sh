#!/bin/bash

# Build and publish container images to Docker Hub and GitHub Container Registry
# Based on issue #122: Publish Pre-built Container Images for Fast Deployment

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load environment variables from .env if it exists
if [ -f "$PROJECT_ROOT/.env" ]; then
    source "$PROJECT_ROOT/.env"
fi

# Configuration
DOCKERHUB_ORG="${DOCKERHUB_ORG:-}"
GITHUB_ORG="${GITHUB_ORG:-}"
GITHUB_REGISTRY="ghcr.io"

# Version management
VERSION="${VERSION:-latest}"
BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
COMMIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')

# Platforms to build for
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

# Components to build
declare -a COMPONENTS=(
    "registry:.:./docker/Dockerfile.registry"
    "auth-server:.:./docker/Dockerfile.auth"
    "currenttime-server:servers/currenttime:./docker/Dockerfile.mcp-server"
    "realserverfaketools-server:servers/realserverfaketools:./docker/Dockerfile.mcp-server"
    "fininfo-server:servers/fininfo:./docker/Dockerfile.mcp-server"
    "mcpgw-server:servers/mcpgw:./docker/Dockerfile.mcp-server"
)

# External images to mirror (pull from source and push to our registries)
declare -a EXTERNAL_IMAGES=(
    "atlassian:ghcr.io/sooperset/mcp-atlassian:latest"
    "postgres:postgres:16-alpine"
    "prometheus:prom/prometheus:latest"
    "grafana:grafana/grafana:latest"
    "alpine:alpine:latest"
)

# Map component names to actual server directory paths
declare -A SERVER_PATH_MAP=(
    ["currenttime-server"]="servers/currenttime"
    ["realserverfaketools-server"]="servers/realserverfaketools"
    ["fininfo-server"]="servers/fininfo"
    ["mcpgw-server"]="servers/mcpgw"
)

# Function to print colored output
print_color() {
    local color=$1
    shift
    echo -e "${color}$@${NC}"
}

# Function to print section headers
print_header() {
    echo ""
    print_color "$BLUE" "=========================================="
    print_color "$BLUE" "$1"
    print_color "$BLUE" "=========================================="
    echo ""
}

# Function to check if Docker is available
check_docker() {
    if ! docker --version &> /dev/null; then
        print_color "$RED" "❌ Docker is not available. Please install Docker."
        exit 1
    fi
}

# Function to setup Docker for building (no buildx needed)
setup_docker() {
    print_color "$GREEN" "✅ Using standard Docker build (no buildx required)"
    print_color "$YELLOW" "⚠️  Note: Building for current platform only (not multi-platform)"
}

# Function to login to Docker Hub
login_dockerhub() {
    if [ -z "$DOCKERHUB_USERNAME" ] || [ -z "$DOCKERHUB_TOKEN" ]; then
        print_color "$YELLOW" "⚠️  Docker Hub credentials not found in environment variables."
        print_color "$YELLOW" "   Please set DOCKERHUB_USERNAME and DOCKERHUB_TOKEN"
        print_color "$YELLOW" "   Attempting to use existing Docker login..."

        # Check if already logged in
        if ! docker pull "$DOCKERHUB_ORG/registry:latest" &> /dev/null; then
            print_color "$RED" "❌ Not logged in to Docker Hub. Please login first:"
            print_color "$YELLOW" "   docker login"
            return 1
        fi
    else
        print_color "$GREEN" "✅ Logging in to Docker Hub..."
        echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin
    fi
}

# Function to login to GitHub Container Registry
login_ghcr() {
    if [ -z "$GITHUB_TOKEN" ]; then
        print_color "$YELLOW" "⚠️  GITHUB_TOKEN not found in environment variables."
        print_color "$YELLOW" "   Skipping GitHub Container Registry push."
        return 1
    else
        print_color "$GREEN" "✅ Logging in to GitHub Container Registry..."
        echo "$GITHUB_TOKEN" | docker login "$GITHUB_REGISTRY" -u "$GITHUB_USERNAME" --password-stdin
    fi
}

# Function to generate tags for an image
generate_tags() {
    local base_name=$1
    local registry=$2
    local tags=""

    # Always include latest tag
    tags="$tags --tag $registry/$base_name:latest"

    # Add version tag if not "latest"
    if [ "$VERSION" != "latest" ]; then
        tags="$tags --tag $registry/$base_name:$VERSION"

        # Add major.minor tag if version is semver
        if [[ "$VERSION" =~ ^v?([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
            major="${BASH_REMATCH[1]}"
            minor="${BASH_REMATCH[2]}"
            tags="$tags --tag $registry/$base_name:$major.$minor"
            tags="$tags --tag $registry/$base_name:$major"
        fi
    fi

    # Add branch tag if not main/master
    if [ "$BRANCH_NAME" != "main" ] && [ "$BRANCH_NAME" != "master" ] && [ "$BRANCH_NAME" != "unknown" ]; then
        # Sanitize branch name for Docker tag
        sanitized_branch=$(echo "$BRANCH_NAME" | sed 's/[^a-zA-Z0-9._-]/-/g')
        tags="$tags --tag $registry/$base_name:$sanitized_branch"
    fi

    # Add commit SHA tag
    if [ "$COMMIT_SHA" != "unknown" ]; then
        tags="$tags --tag $registry/$base_name:sha-$COMMIT_SHA"
    fi

    echo "$tags"
}

# Function to build and push a single component
build_and_push_component() {
    local component_info=$1
    local push_dockerhub=$2
    local push_ghcr=$3

    IFS=':' read -r name context dockerfile <<< "$component_info"

    print_color "$BLUE" "📦 Building $name..."
    print_color "$YELLOW" "   Context: $context"
    print_color "$YELLOW" "   Dockerfile: $dockerfile"

    # Check if Dockerfile exists
    if [ ! -f "$PROJECT_ROOT/$dockerfile" ]; then
        print_color "$RED" "❌ Dockerfile not found: $PROJECT_ROOT/$dockerfile"
        return 1
    fi

    # Generate all tags
    local all_tags=""

    if [ "$push_dockerhub" = true ]; then
        # Use organization if set, otherwise use username for personal account
        if [ -n "$DOCKERHUB_ORG" ]; then
            dockerhub_base="$DOCKERHUB_ORG/$name"
        else
            dockerhub_base="$DOCKERHUB_USERNAME/$name"
        fi
        dockerhub_tags=$(generate_tags "$dockerhub_base" "docker.io")
        all_tags="$all_tags $dockerhub_tags"
    fi

    if [ "$push_ghcr" = true ]; then
        # Use organization if set, otherwise use username for personal account
        if [ -n "$GITHUB_ORG" ]; then
            ghcr_base="$GITHUB_ORG/mcp-$name"
        else
            ghcr_base="$GITHUB_USERNAME/mcp-$name"
        fi
        ghcr_tags=$(generate_tags "$ghcr_base" "$GITHUB_REGISTRY")
        all_tags="$all_tags $ghcr_tags"
    fi

    # Build and push with buildx
    print_color "$GREEN" "✅ Building for platforms: $PLATFORMS"

    local push_flag=""
    if [ "$push_dockerhub" = true ] || [ "$push_ghcr" = true ]; then
        push_flag="--push"
    fi

    cd "$PROJECT_ROOT"

    # Build the image first
    print_color "$GREEN" "✅ Building image..."

    # Add SERVER_PATH build arg for MCP servers
    local build_args=""
    if [[ "$dockerfile" == *"Dockerfile.mcp-server"* ]]; then
        # Use the mapped server path if available, otherwise fallback to the component name
        local server_path="${SERVER_PATH_MAP[$name]:-servers/$name}"
        build_args="--build-arg SERVER_PATH=$server_path"
        print_color "$YELLOW" "   Adding build arg: SERVER_PATH=$server_path"
    fi

    docker build \
        --file "$dockerfile" \
        $build_args \
        --label "org.opencontainers.image.created=$BUILD_DATE" \
        --label "org.opencontainers.image.source=https://github.com/agentic-community/mcp-gateway-registry" \
        --label "org.opencontainers.image.version=$VERSION" \
        --label "org.opencontainers.image.revision=$COMMIT_SHA" \
        --label "org.opencontainers.image.title=MCP Gateway $name" \
        --label "org.opencontainers.image.description=MCP Gateway Registry - $name component" \
        --label "org.opencontainers.image.vendor=Agentic Community" \
        --tag "local/$name:$VERSION" \
        "$context"

    if [ $? -ne 0 ]; then
        print_color "$RED" "❌ Failed to build $name"
        return 1
    fi

    # Tag and push images if needed
    if [ "$push_dockerhub" = true ] || [ "$push_ghcr" = true ]; then
        print_color "$GREEN" "✅ Tagging and pushing images..."

        # Parse all tags and push them
        # Convert the tag string to an array
        eval "tag_array=($all_tags)"
        i=0
        while [ $i -lt ${#tag_array[@]} ]; do
            if [ "${tag_array[$i]}" = "--tag" ]; then
                # Next element is the tag value
                i=$((i + 1))
                if [ $i -lt ${#tag_array[@]} ]; then
                    tag_value="${tag_array[$i]}"
                    print_color "$YELLOW" "  Tagging: $tag_value"
                    docker tag "local/$name:$VERSION" "$tag_value"

                    if [ "$push_dockerhub" = true ] || [ "$push_ghcr" = true ]; then
                        print_color "$YELLOW" "  Pushing: $tag_value"
                        docker push "$tag_value"
                    fi
                fi
            fi
            i=$((i + 1))
        done
    fi

    if [ $? -eq 0 ]; then
        print_color "$GREEN" "✅ Successfully built and pushed $name"
    else
        print_color "$RED" "❌ Failed to build and push $name"
        return 1
    fi
}

# Function to mirror external images
mirror_external_image() {
    local image_info=$1
    local push_dockerhub=$2
    local push_ghcr=$3

    IFS=':' read -r name source_image <<< "$image_info"

    print_color "$BLUE" "🔄 Mirroring $name from $source_image..."

    # Pull the source image
    print_color "$YELLOW" "  Pulling: $source_image"
    if ! docker pull "$source_image"; then
        print_color "$RED" "❌ Failed to pull $source_image"
        return 1
    fi

    # Tag and push to registries
    if [ "$push_dockerhub" = true ]; then
        if [ -n "$DOCKERHUB_ORG" ]; then
            dockerhub_target="$DOCKERHUB_ORG/$name:latest"
        else
            dockerhub_target="$DOCKERHUB_USERNAME/$name:latest"
        fi

        print_color "$YELLOW" "  Tagging: $dockerhub_target"
        docker tag "$source_image" "$dockerhub_target"

        print_color "$YELLOW" "  Pushing: $dockerhub_target"
        if ! docker push "$dockerhub_target"; then
            print_color "$RED" "❌ Failed to push to Docker Hub"
            return 1
        fi

        # Also tag with version if not latest
        if [ "$VERSION" != "latest" ]; then
            if [ -n "$DOCKERHUB_ORG" ]; then
                dockerhub_version_target="$DOCKERHUB_ORG/$name:$VERSION"
            else
                dockerhub_version_target="$DOCKERHUB_USERNAME/$name:$VERSION"
            fi
            docker tag "$source_image" "$dockerhub_version_target"
            docker push "$dockerhub_version_target"
        fi
    fi

    if [ "$push_ghcr" = true ]; then
        if [ -n "$GITHUB_ORG" ]; then
            ghcr_target="$GITHUB_REGISTRY/$GITHUB_ORG/mcp-$name:latest"
        else
            ghcr_target="$GITHUB_REGISTRY/$GITHUB_USERNAME/mcp-$name:latest"
        fi

        print_color "$YELLOW" "  Tagging: $ghcr_target"
        docker tag "$source_image" "$ghcr_target"

        print_color "$YELLOW" "  Pushing: $ghcr_target"
        if ! docker push "$ghcr_target"; then
            print_color "$RED" "❌ Failed to push to GHCR"
            return 1
        fi

        # Also tag with version if not latest
        if [ "$VERSION" != "latest" ]; then
            if [ -n "$GITHUB_ORG" ]; then
                ghcr_version_target="$GITHUB_REGISTRY/$GITHUB_ORG/mcp-$name:$VERSION"
            else
                ghcr_version_target="$GITHUB_REGISTRY/$GITHUB_USERNAME/mcp-$name:$VERSION"
            fi
            docker tag "$source_image" "$ghcr_version_target"
            docker push "$ghcr_version_target"
        fi
    fi

    print_color "$GREEN" "✅ Successfully mirrored $name"
    return 0
}

# Function to display usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Build and publish MCP Gateway Registry container images to Docker Hub and GitHub Container Registry.

OPTIONS:
    -d, --dockerhub     Push to Docker Hub (requires DOCKERHUB_USERNAME and DOCKERHUB_TOKEN)
    -g, --ghcr          Push to GitHub Container Registry (requires GITHUB_TOKEN)
    -v, --version       Version tag (default: latest)
    -p, --platforms     Platforms to build for (note: only current platform supported without buildx)
    -c, --component     Build specific component only (registry, auth-server, nginx-proxy, currenttime-server, realserverfaketools)
    -s, --skip-mirror   Skip mirroring external images (by default, external images ARE mirrored)
    -l, --local         Build locally without pushing (for testing)
    -h, --help          Display this help message

ENVIRONMENT VARIABLES:
    DOCKERHUB_USERNAME  Docker Hub username
    DOCKERHUB_TOKEN     Docker Hub access token
    GITHUB_USERNAME     GitHub username (defaults to current git user)
    GITHUB_TOKEN        GitHub personal access token with write:packages permission
    DOCKERHUB_ORG       Docker Hub organization (default: mcpgateway)
    GITHUB_ORG          GitHub organization (default: agentic-community)
    VERSION             Version tag (default: latest)
    PLATFORMS           Build platforms (note: only current platform supported without buildx)

EXAMPLES:
    # Build and push everything to both registries (includes external images by default)
    $0 --dockerhub --ghcr --version v1.0.0

    # Build and push to Docker Hub only (includes external images)
    $0 --dockerhub

    # Build specific component only (skips external images)
    $0 --dockerhub --component registry

    # Build and push WITHOUT mirroring external images
    $0 --dockerhub --skip-mirror

    # Build locally for testing (no push)
    $0 --local

    # Build with custom platforms
    $0 --dockerhub --platforms linux/amd64

EOF
}

# Parse command line arguments
PUSH_DOCKERHUB=false
PUSH_GHCR=false
BUILD_LOCAL=false
MIRROR_EXTERNAL=true  # Default to TRUE - mirror by default
SPECIFIC_COMPONENT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--dockerhub)
            PUSH_DOCKERHUB=true
            shift
            ;;
        -g|--ghcr)
            PUSH_GHCR=true
            shift
            ;;
        -v|--version)
            VERSION="$2"
            shift 2
            ;;
        -p|--platforms)
            PLATFORMS="$2"
            shift 2
            ;;
        -c|--component)
            SPECIFIC_COMPONENT="$2"
            shift 2
            ;;
        -s|--skip-mirror)
            MIRROR_EXTERNAL=false
            shift
            ;;
        -l|--local)
            BUILD_LOCAL=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            print_color "$RED" "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Main execution
print_header "MCP Gateway Registry Container Publisher"

print_color "$BLUE" "Configuration:"
print_color "$YELLOW" "  Version:        $VERSION"
print_color "$YELLOW" "  Branch:         $BRANCH_NAME"
print_color "$YELLOW" "  Commit:         $COMMIT_SHA"
print_color "$YELLOW" "  Platforms:      $PLATFORMS"
print_color "$YELLOW" "  Docker Hub Org: $DOCKERHUB_ORG"
print_color "$YELLOW" "  GitHub Org:     $GITHUB_ORG"
echo ""

# Check if any action is specified
if [ "$PUSH_DOCKERHUB" = false ] && [ "$PUSH_GHCR" = false ] && [ "$BUILD_LOCAL" = false ]; then
    print_color "$RED" "❌ No action specified. Use --dockerhub, --ghcr, or --local"
    usage
    exit 1
fi

# Setup Docker
print_header "Setting up Docker"
check_docker
setup_docker

# Login to registries if needed
if [ "$PUSH_DOCKERHUB" = true ]; then
    print_header "Docker Hub Authentication"
    if ! login_dockerhub; then
        print_color "$RED" "❌ Failed to login to Docker Hub"
        exit 1
    fi
fi

if [ "$PUSH_GHCR" = true ]; then
    print_header "GitHub Container Registry Authentication"

    # Get GitHub username if not set
    if [ -z "$GITHUB_USERNAME" ]; then
        GITHUB_USERNAME=$(git config --get user.name 2>/dev/null || echo "")
        if [ -z "$GITHUB_USERNAME" ]; then
            print_color "$RED" "❌ GITHUB_USERNAME not set and couldn't determine from git config"
            exit 1
        fi
    fi

    if ! login_ghcr; then
        print_color "$YELLOW" "⚠️  Skipping GitHub Container Registry"
        PUSH_GHCR=false
    fi
fi

# Build and push components
print_header "Building and Publishing Container Images"

# Track success/failure
declare -a failed_components=()
declare -a successful_components=()

# Build components
for component_info in "${COMPONENTS[@]}"; do
    component_name=$(echo "$component_info" | cut -d':' -f1)

    # Skip if specific component is requested and this isn't it
    if [ -n "$SPECIFIC_COMPONENT" ] && [ "$component_name" != "$SPECIFIC_COMPONENT" ]; then
        continue
    fi

    print_color "$BLUE" "Building $component_name..."

    if build_and_push_component "$component_info" "$PUSH_DOCKERHUB" "$PUSH_GHCR"; then
        successful_components+=("$component_name")
    else
        failed_components+=("$component_name")
    fi

    echo ""
done

# Mirror external images if requested (skip if building specific component)
if [ "$MIRROR_EXTERNAL" = true ] && [ -z "$SPECIFIC_COMPONENT" ]; then
    print_header "Mirroring External Container Images"

    for image_info in "${EXTERNAL_IMAGES[@]}"; do
        image_name=$(echo "$image_info" | cut -d':' -f1)

        print_color "$BLUE" "Mirroring $image_name..."

        if mirror_external_image "$image_info" "$PUSH_DOCKERHUB" "$PUSH_GHCR"; then
            successful_components+=("$image_name (mirrored)")
        else
            failed_components+=("$image_name (mirrored)")
        fi

        echo ""
    done
fi

# Summary
print_header "Build Summary"

if [ ${#successful_components[@]} -gt 0 ]; then
    print_color "$GREEN" "✅ Successfully built and pushed:"
    for component in "${successful_components[@]}"; do
        print_color "$GREEN" "   - $component"
    done
fi

if [ ${#failed_components[@]} -gt 0 ]; then
    print_color "$RED" "❌ Failed to build:"
    for component in "${failed_components[@]}"; do
        print_color "$RED" "   - $component"
    done
    exit 1
fi

print_color "$GREEN" ""
print_color "$GREEN" "🎉 All components built and pushed successfully!"

if [ "$PUSH_DOCKERHUB" = true ]; then
    print_color "$BLUE" ""
    print_color "$BLUE" "Docker Hub images:"
    for component_info in "${COMPONENTS[@]}"; do
        component_name=$(echo "$component_info" | cut -d':' -f1)
        if [ -n "$SPECIFIC_COMPONENT" ] && [ "$component_name" != "$SPECIFIC_COMPONENT" ]; then
            continue
        fi
        if [ -n "$DOCKERHUB_ORG" ]; then
            print_color "$YELLOW" "  docker pull $DOCKERHUB_ORG/$component_name:$VERSION"
        else
            print_color "$YELLOW" "  docker pull $DOCKERHUB_USERNAME/$component_name:$VERSION"
        fi
    done

    # Show mirrored external images
    if [ "$MIRROR_EXTERNAL" = true ]; then
        print_color "$BLUE" ""
        print_color "$BLUE" "Mirrored External Images:"
        for image_info in "${EXTERNAL_IMAGES[@]}"; do
            image_name=$(echo "$image_info" | cut -d':' -f1)
            if [ -n "$DOCKERHUB_ORG" ]; then
                print_color "$YELLOW" "  docker pull $DOCKERHUB_ORG/$image_name:latest"
            else
                print_color "$YELLOW" "  docker pull $DOCKERHUB_USERNAME/$image_name:latest"
            fi
        done
    fi
fi

if [ "$PUSH_GHCR" = true ]; then
    print_color "$BLUE" ""
    print_color "$BLUE" "GitHub Container Registry images:"
    for component_info in "${COMPONENTS[@]}"; do
        component_name=$(echo "$component_info" | cut -d':' -f1)
        if [ -n "$SPECIFIC_COMPONENT" ] && [ "$component_name" != "$SPECIFIC_COMPONENT" ]; then
            continue
        fi
        if [ -n "$GITHUB_ORG" ]; then
            print_color "$YELLOW" "  docker pull $GITHUB_REGISTRY/$GITHUB_ORG/mcp-$component_name:$VERSION"
        else
            print_color "$YELLOW" "  docker pull $GITHUB_REGISTRY/$GITHUB_USERNAME/mcp-$component_name:$VERSION"
        fi
    done

    # Show mirrored external images
    if [ "$MIRROR_EXTERNAL" = true ]; then
        print_color "$BLUE" ""
        print_color "$BLUE" "Mirrored External Images:"
        for image_info in "${EXTERNAL_IMAGES[@]}"; do
            image_name=$(echo "$image_info" | cut -d':' -f1)
            if [ -n "$GITHUB_ORG" ]; then
                print_color "$YELLOW" "  docker pull $GITHUB_REGISTRY/$GITHUB_ORG/mcp-$image_name:latest"
            else
                print_color "$YELLOW" "  docker pull $GITHUB_REGISTRY/$GITHUB_USERNAME/mcp-$image_name:latest"
            fi
        done
    fi
fi
