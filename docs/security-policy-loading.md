# Security: Dynamic Policy Loading

Luthien Proxy loads policy classes dynamically at runtime. This enables flexible policy management but introduces security-sensitive code paths that operators must understand and protect.

This document covers how policy loading works, the security implications, and required mitigations for production deployments.

## How Dynamic Policy Loading Works

### Startup: POLICY_CONFIG Environment Variable

At startup, the proxy loads a policy through a chain of resolution:

1. **Environment variable** `POLICY_CONFIG` points to a YAML file path (default: `config/policy_config.yaml`)
2. **YAML file** contains a `policy.class` field with a Python module reference (e.g., `luthien_proxy.policies.noop_policy:NoOpPolicy`)
3. **Python's `__import__`** dynamically imports the module and loads the class
4. The class is instantiated with any `config` parameters from the YAML

Example YAML:

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    model: "openai/gpt-4o-mini"
    probability_threshold: 0.01
```

The `POLICY_SOURCE` environment variable controls where the policy is loaded from:

| Value | Behavior |
|-------|----------|
| `db-fallback-file` | Try database first, fall back to YAML file (default) |
| `file-fallback-db` | Try YAML file first, fall back to database |
| `db` | Database only |
| `file` | YAML file only, persisted to database |

### Runtime: Admin API

The admin API allows changing the active policy without restarting the proxy:

- **`POST /admin/policy/set`** accepts a `policy_class_ref` string and `config` dict, dynamically imports the class, validates config, instantiates it, and persists the selection to the database.
- **`GET /admin/policy/current`** returns the active policy and its configuration.
- **`GET /admin/policy/list`** discovers all policy classes in the `luthien_proxy.policies` package.

### Class Resolution Internals

The function `_import_policy_class` in `src/luthien_proxy/config.py` performs the import:

1. Splits the reference on `:` (e.g., `module.path:ClassName`)
2. Calls `__import__(module_path, fromlist=[class_name])` to import the module
3. Uses `getattr` to retrieve the class
4. Validates the class is a subclass of `BasePolicy`

The `BasePolicy` subclass check is the only validation on the class reference. There is no allowlist of permitted modules or packages.

## Security Implications

### Arbitrary Code Execution via POLICY_CONFIG

**Risk:** If an attacker can modify the YAML file referenced by `POLICY_CONFIG`, or change the `POLICY_CONFIG` environment variable itself, they can cause the proxy to import and execute arbitrary Python code.

The `__import__` call will import any Python module reachable on the Python path. While the `BasePolicy` subclass check prevents loading classes that do not inherit from `BasePolicy`, a malicious module's top-level code executes during import, before the subclass check runs. An attacker could craft a module whose import side-effects run arbitrary code.

**Attack scenarios:**
- Modifying the YAML config file on disk
- Changing the `POLICY_CONFIG` environment variable to point to a malicious YAML file
- Placing a malicious Python module on the Python path and referencing it in the config

### Arbitrary Code Execution via Admin API

**Risk:** The `POST /admin/policy/set` endpoint accepts any `policy_class_ref` string. An attacker with access to this endpoint can trigger the same dynamic import path.

The admin API is protected by `ADMIN_API_KEY`, verified via `verify_admin_token` in `src/luthien_proxy/auth.py`. Authentication supports:
- Session cookie (browser login)
- Bearer token in Authorization header
- `x-api-key` header

All comparisons use `secrets.compare_digest` (constant-time) to prevent timing attacks.

### Policy Config Leaks Operational Details

The `GET /admin/policy/current` endpoint returns the full policy configuration, which may include model names, API endpoints, thresholds, and other operational parameters. This is useful for administration but should not be exposed to untrusted parties.

## Required Mitigations

### 1. Protect the ADMIN_API_KEY

The admin API key is the primary control gate for runtime policy changes. Treat it with the same sensitivity as database credentials.

- **Generate a strong key:** Use at least 32 random characters (e.g., `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- **Never commit it to source control.** Use environment variables or a secrets manager.
- **Rotate periodically** and after any suspected compromise.
- **Limit distribution.** Only operators who need to change policies should have the key.

If `ADMIN_API_KEY` is not set, the admin endpoints return HTTP 500. This is a fail-closed design.

### 2. Restrict File Permissions on Policy Config

The YAML file should be readable only by the gateway process:

```bash
# On the host (or in the Docker image)
chmod 600 config/policy_config.yaml
chown <gateway-user>:<gateway-group> config/policy_config.yaml
```

In the Docker deployment, `config/` is mounted read-only into the container (`./config:/app/config:ro` pattern). Ensure the host directory has appropriate permissions.

### 3. Restrict Network Access to Admin Endpoints

The `/admin/*` endpoints should not be reachable from the public internet.

**Recommended approaches:**

- **Reverse proxy rules:** Block `/admin/*` paths at the load balancer or reverse proxy (nginx, Caddy, cloud ALB) so they are only accessible from internal networks.
- **Network segmentation:** Use VPC security groups, firewall rules, or Docker network isolation to restrict access to the admin port.
- **VPN or bastion host:** Require operators to connect via VPN before accessing admin endpoints.

Even with `ADMIN_API_KEY` authentication, defense in depth requires network-level controls. API keys can be leaked, logged, or brute-forced given enough time.

### 4. Secure the Database

The `current_policy` database table stores the active policy class reference. An attacker with write access to this table can change the policy loaded at next startup (when `POLICY_SOURCE` includes `db`).

- Use strong, unique database credentials.
- Restrict database network access to the gateway and authorized admin tools only.
- Enable TLS for database connections in production.

### 5. Restrict the Python Path

Since `__import__` loads any module on `sys.path`, limit what code is available:

- In Docker deployments, avoid mounting untrusted directories into the container.
- Do not install unnecessary Python packages in the production image.
- Use a minimal base image with only required dependencies.

## Production Deployment Checklist

| Item | Status |
|------|--------|
| `ADMIN_API_KEY` set to a strong, unique value | |
| `ADMIN_API_KEY` stored in a secrets manager (not in code or config files) | |
| `/admin/*` endpoints blocked at the network/reverse-proxy level from public access | |
| `config/policy_config.yaml` is read-only and owned by the gateway process | |
| Database credentials are strong and stored securely | |
| Database network access is restricted to the gateway | |
| `POLICY_SOURCE` is set deliberately (not relying on defaults) | |
| Container image uses minimal base with no unnecessary packages | |
| No untrusted volumes mounted into the gateway container | |
| TLS enabled for all external-facing connections (gateway, database, Redis) | |

## Reference

| File | Role |
|------|------|
| `src/luthien_proxy/config.py` | `_import_policy_class` -- dynamic import, `BasePolicy` subclass check |
| `src/luthien_proxy/policy_manager.py` | Startup loading strategies, `enable_policy` for runtime swaps |
| `src/luthien_proxy/admin/routes.py` | Admin API endpoints (`/admin/policy/set`, `/admin/policy/current`, `/admin/policy/list`) |
| `src/luthien_proxy/auth.py` | `verify_admin_token` -- admin authentication logic |
| `src/luthien_proxy/settings.py` | Environment variable definitions (`POLICY_CONFIG`, `ADMIN_API_KEY`, etc.) |
| `config/policy_config.yaml` | Default policy configuration file |
