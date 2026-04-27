# Configuration Reference

This page is a simplified index of the configuration files you actually need.
Use the links below to open the source files directly in GitHub.

## Active Configuration Files

| File | Purpose | What to do |
|---|---|---|
| [`.env`](https://github.com/ascending-llc/jarvis-registry/blob/main/.env.example) | Main runtime configuration | Copy from `.env.example` into `.env` and set your values |
| [`oauth2_providers.yml`](https://github.com/ascending-llc/jarvis-registry/blob/main/auth-server/src/auth_server/oauth2_providers.yml) | Identity provider (IdP) configuration | Adjust only if you need custom IdP behavior |
| [`scopes.yml`](https://github.com/ascending-llc/jarvis-registry/blob/main/registry-pkgs/src/registry_pkgs/scopes.yml) | RBAC scope and group mapping configuration | Update only when changing authorization rules |
| [`docker-compose.yml`](https://github.com/ascending-llc/jarvis-registry/blob/main/docker-compose.yml) | Base service definitions for Docker deployment | Usually keep as-is |
| [`docker-compose.override.yml.example`](https://github.com/ascending-llc/jarvis-registry/blob/main/docker-compose.override.yml.example) | User override template for Docker services | Copy to `docker-compose.override.yml` and customize locally |

## Quick Setup

1. Create your main environment file:

   ```bash
   cp .env.example .env
   ```

2. (Optional) Create a local Docker override:

   ```bash
   cp docker-compose.override.yml.example docker-compose.override.yml
   ```

3. Edit only what you need in `.env` and `docker-compose.override.yml`.

## Notes

- OAuth credential and AgentCore-specific setup content was removed from this page because those flows are now handled in the interface.
- `oauth2_providers.yml` is the IdP configuration source for authentication providers.
- `oauth_providers.yaml` is intentionally not included here.
