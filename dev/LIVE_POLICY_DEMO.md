# Live Policy Updates in Claude Code

This guide demonstrates how to perform live policy updates within an active Claude Code session through the Luthien proxy.

## Setup

Ensure Claude Code is running through the proxy:

```bash
./scripts/launch_claude_code.sh
```

## Demo: Switching Policies Without Restart

### 1. Start with a baseline policy

Check the current active policy:

```bash
curl http://localhost:8000/admin/policy/current \
  -H "Authorization: Bearer admin-dev-key" | jq '.policy'
```

### 2. Update to AllCapsPolicy

Create and activate the AllCapsPolicy:

```bash
# Create the policy instance
curl -X POST http://localhost:8000/admin/policy/create \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{"name": "demo-allcaps", "policy_class_ref": "luthien_proxy.policies.all_caps_policy:AllCapsPolicy", "config": {}}'

# Activate it
curl -X POST http://localhost:8000/admin/policy/activate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{"name": "demo-allcaps"}'
```

### 3. Observe the effect in Claude Code

Notice that Claude Code's responses are now in ALL CAPS. The policy update took effect immediately without restarting Claude Code or the gateway—the proxy hot-reloads the policy and all subsequent requests flow through the new policy.

### 4. Switch back to a no-op policy

```bash
# Create a NoOp policy instance
curl -X POST http://localhost:8000/admin/policy/create \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{"name": "demo-noop", "policy_class_ref": "luthien_proxy.policies.simple_noop_policy:SimpleNoOpPolicy", "config": {}}'

# Activate it
curl -X POST http://localhost:8000/admin/policy/activate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer admin-dev-key" \
  -d '{"name": "demo-noop"}'
```

Claude Code responses return to normal case immediately.

## Why This Matters

- **No restart required**: Policy changes take effect instantly across all active connections
- **Live development**: Test new policies without stopping your workflow
- **Production-ready**: Hot-reload capability supports zero-downtime policy updates
- **Debugging**: Quickly switch between policies to diagnose issues

## Other Available Policies

List all available policy classes:

```bash
curl http://localhost:8000/admin/policy/list \
  -H "Authorization: Bearer admin-dev-key" | jq '.policies'
```

Create custom policies by extending the base policy interface in `src/luthien_proxy/policies/` and they will automatically appear in the list.
