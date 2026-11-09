# MCP Gateway Registry API Documentation

This document provides a comprehensive overview of all API endpoints available in the MCP Gateway Registry service.

## Table of Contents

- [Authentication](#authentication)
  - [Login Form](#login-form)
  - [Login Submission](#login-submission)
  - [Logout](#logout)
- [Server Management](#server-management)
  - [Register a New Service](#register-a-new-service)
  - [Toggle Service Status](#toggle-service-status)
  - [Edit Service Details](#edit-service-details)
- [API Endpoints](#api-endpoints)
  - [Get Server Details](#get-server-details)
  - [Get Service Tools](#get-service-tools)
  - [Refresh Service](#refresh-service)

## Authentication

> **IMPORTANT**: Most endpoints in this API require authentication. You must first call the `/login` endpoint to obtain a session cookie, which must be included in all subsequent requests to authenticated endpoints. The examples below use `-b cookies.txt` to include the session cookie in the requests.

### Login Form

Displays the login form for the MCP Gateway Registry.

**URL:** `/login`
**Method:** `GET`
**Response:** HTML login form

**Example:**

```bash
curl -X GET http://localhost:7860/login
```

### Login Submission

Authenticates a user and creates a session. **This endpoint must be called first** to obtain the session cookie required for all other authenticated endpoints.

**URL:** `/login`
**Method:** `POST`
**Content-Type:** `application/x-www-form-urlencoded`
**Parameters:**
- `username` (required): Admin username
- `password` (required): Admin password

**Response:**
- Success: Redirects to `/` with a session cookie
- Failure: Redirects to `/login?error=Invalid+username+or+password`

**Example:**

```bash
# Save the session cookie to cookies.txt for use in subsequent requests
curl -X POST http://localhost:7860/login \
  -d "username=admin&password=password" \
  -c cookies.txt
```

### Logout

Logs out the current user by invalidating their session.

**URL:** `/logout`
**Method:** `POST`
**Authentication:** Required (session cookie)
**Response:** Redirects to `/login`

**Example:**

```bash
curl -X POST http://localhost:7860/logout \
  -b cookies.txt
```

## Server Management

> **Note**: All endpoints in this section require authentication via the session cookie obtained from the `/login` endpoint.

### Register a New Service

Registers a new MCP service with the gateway.

**URL:** `/register`
**Method:** `POST`
**Content-Type:** `application/x-www-form-urlencoded`
**Authentication:** Required (session cookie)
**Parameters:**
- `name` (required): Display name of the service
- `description` (required): Description of the service
- `path` (required): URL path for the service
- `proxy_pass_url` (required): URL to proxy requests to
- `tags` (optional): Comma-separated list of tags
- `num_tools` (optional): Number of tools provided by the service
- `num_stars` (optional): Star rating for the service
- `is_python` (optional): Whether the service is Python-based
- `license` (optional): License information

**Response:**
- Success: JSON response with status code 201
- Failure: JSON response with error details

**Example:**

```bash
# Uses the session cookie from the login request
curl -X POST http://localhost:7860/register \
  -b cookies.txt \
  -d "name=Weather Service&description=Provides weather forecasts&path=/weather&proxy_pass_url=http://localhost:8000&tags=weather,forecast&num_tools=3&num_stars=4&is_python=true&license=MIT"
```

### Toggle Service Status

Enables or disables a registered service.

**URL:** `/toggle/{service_path}`
**Method:** `POST`
**Content-Type:** `application/x-www-form-urlencoded`
**Authentication:** Required (session cookie)
**URL Parameters:**
- `service_path`: Path of the service to toggle
**Form Parameters:**
- `enabled`: "on" to enable, omit to disable

**Response:** JSON with updated service status

**Example:**

```bash
# Enable a service (requires session cookie)
curl -X POST http://localhost:7860/toggle/weather \
  -b cookies.txt \
  -d "enabled=on"

# Disable a service (requires session cookie)
curl -X POST http://localhost:7860/toggle/weather \
  -b cookies.txt
```

### Edit Service Details

Updates the details of an existing service.

**URL:** `/edit/{service_path}`
**Method:** `POST`
**Content-Type:** `application/x-www-form-urlencoded`
**Authentication:** Required (session cookie)
**URL Parameters:**
- `service_path`: Path of the service to edit
**Form Parameters:**
- `name` (required): Display name of the service
- `proxy_pass_url` (required): URL to proxy requests to
- `description` (optional): Description of the service
- `tags` (optional): Comma-separated list of tags
- `num_tools` (optional): Number of tools provided by the service
- `num_stars` (optional): Star rating for the service
- `is_python` (optional): Whether the service is Python-based
- `license` (optional): License information

**Response:** Redirects to the main page on success

**Example:**

```bash
# Requires session cookie from login
curl -X POST http://localhost:7860/edit/weather \
  -b cookies.txt \
  -d "name=Weather API&description=Updated weather service&proxy_pass_url=http://localhost:8001&tags=weather,api&num_tools=5&num_stars=5&is_python=true&license=MIT"
```

## API Endpoints

> **Note**: All endpoints in this section require authentication via the session cookie obtained from the `/login` endpoint.

### Get Server Details

Retrieves detailed information about a registered service.

**URL:** `/api/server_details/{service_path}`
**Method:** `GET`
**Authentication:** Required (session cookie)
**URL Parameters:**
- `service_path`: Path of the service to get details for, or "all" to get details for all services

**Response:** JSON with server details

**Example:**

```bash
# Get details for a specific service (requires session cookie)
curl -X GET http://localhost:7860/api/server_details/weather \
  -b cookies.txt

# Get details for all services (requires session cookie)
curl -X GET http://localhost:7860/api/server_details/all \
  -b cookies.txt
```

### Get Service Tools

Retrieves the list of tools provided by a service.

**URL:** `/api/tools/{service_path}`
**Method:** `GET`
**Authentication:** Required (session cookie)
**URL Parameters:**
- `service_path`: Path of the service to get tools for, or "all" to get tools from all services

**Response:** JSON with tool details

**Example:**

```bash
# Get tools for a specific service (requires session cookie)
curl -X GET http://localhost:7860/api/tools/weather \
  -b cookies.txt

# Get tools from all services (requires session cookie)
curl -X GET http://localhost:7860/api/tools/all \
  -b cookies.txt
```

### Refresh Service

Manually triggers a health check and tool discovery for a service.

**URL:** `/api/refresh/{service_path}`
**Method:** `POST`
**Authentication:** Required (session cookie)
**URL Parameters:**
- `service_path`: Path of the service to refresh

**Response:** JSON with updated service status

**Example:**

```bash
# Requires session cookie from login
curl -X POST http://localhost:7860/api/refresh/weather \
  -b cookies.txt
```

## Authentication Flow

1. **First Step**: Call the `/login` endpoint with valid credentials to obtain a session cookie:
   ```bash
   curl -X POST http://localhost:7860/login \
     -d "username=admin&password=password" \
     -c cookies.txt
   ```

2. **Subsequent Requests**: Include the session cookie in all authenticated API calls:
   ```bash
   curl -X GET http://localhost:7860/api/server_details/all \
     -b cookies.txt
   ```

3. **Session Expiration**: The session cookie is valid for 8 hours. After expiration, you'll need to login again.

## API Summary

* `GET /login`: Display login form.
* `POST /login`: Authenticate user and obtain session cookie (required for all authenticated endpoints).
* `POST /logout`: Log out user and invalidate session cookie.
* `GET /`: Main dashboard (web UI, requires authentication).
* `GET /edit/{service_path}`: Edit service form (web UI, requires authentication).
* `POST /register`: Register a new service (requires authentication).
* `POST /toggle/{service_path}`: Enable/disable a service (requires authentication).
* `POST /edit/{service_path}`: Update service details (requires authentication).
* `GET /api/server_details/{service_path}`: Get full details for a service (requires authentication).
* `GET /api/tools/{service_path}`: Get the discovered tool list for a service (requires authentication).
* `POST /api/refresh/{service_path}`: Manually trigger a health check/tool update (requires authentication).
