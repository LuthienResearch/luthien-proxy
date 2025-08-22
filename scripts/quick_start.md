# Luthien Control Quick Start Guide

This guide will get you up and running with the Luthien Control development environment in just a few minutes.

## Prerequisites

- **Docker Desktop** (or Docker Engine + Docker Compose)
- **Python 3.13+**
- **uv** (Python package manager) - Install from https://docs.astral.sh/uv/

## Quick Setup

1. **Clone and enter the repository:**
   ```bash
   cd luthien-control
   ```

2. **Run the setup script:**
   ```bash
   ./scripts/dev_setup.sh
   ```

   This script will:
   - Create a `.env` file from the template
   - Install Python dependencies
   - Build Docker images
   - Start all services (PostgreSQL, Redis, Ollama, Control Plane, LiteLLM Proxy)
   - Initialize the database
   - Pull the default AI model (llama3.1:8b)

3. **Test the setup:**
   ```bash
   uv run python scripts/test_proxy.py
   ```

## What Gets Started

After setup, you'll have these services running:

| Service | URL | Purpose |
|---------|-----|---------|
| **LiteLLM Proxy** | http://localhost:4000 | OpenAI-compatible API gateway with AI control |
| **Control Plane** | http://localhost:8081 | Policy orchestration and decision engine |
| **PostgreSQL** | localhost:5432 | Persistent storage for policies and audit logs |
| **Redis** | localhost:6379 | Ephemeral state and caching |
| **Ollama** | http://localhost:11434 | Local AI models for trusted supervision |

## Testing Your Setup

### Basic Test
```bash
# Test that all services are healthy
curl http://localhost:4000/health
curl http://localhost:8081/health
```

### AI Control Test
```bash
# Send a request through the proxy (with AI control)
curl -X POST http://localhost:4000/chat/completions \
  -H "Authorization: Bearer sk-luthien-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello! Can you help me write a simple Python script?"}],
    "max_tokens": 200
  }'
```

### Monitor Services
```bash
# Check service status
./scripts/monitor_services.sh

# View logs
docker-compose logs -f
```

## Adding API Keys

To use real LLM providers (OpenAI, Anthropic, etc.), edit your `.env` file:

```bash
# Add your API keys
OPENAI_API_KEY=sk-your-openai-key-here
ANTHROPIC_API_KEY=sk-your-anthropic-key-here
```

Then restart the services:
```bash
docker-compose restart litellm-proxy
```

## Development Workflow

1. **Make code changes** in the `src/` directory
2. **Restart services** to pick up changes:
   ```bash
   docker-compose restart control-plane litellm-proxy
   ```
3. **Test your changes**:
   ```bash
   uv run python scripts/test_proxy.py
   ```
4. **View logs**:
   ```bash
   docker-compose logs -f control-plane litellm-proxy
   ```

## What's Implemented

This Phase 0 implementation provides:

✅ **LiteLLM Proxy** with custom hooks for AI control
✅ **Control Plane** with policy evaluation endpoints
✅ **Database schema** for policies, episodes, and audit logs
✅ **Basic trusted monitoring** using local models
✅ **Docker containerization** for easy deployment
✅ **Health checks** and basic testing

## What's Next (Future Phases)

- **Phase 1**: Enhanced trusted monitoring with request/response editing
- **Phase 2**: Untrusted monitoring with honeypots and paraphrasing
- **Phase 3**: Defer-to-resample protocol implementation
- **Phase 4**: Web UI for policy management and monitoring

## Troubleshooting

### Services won't start
```bash
# Check Docker logs
docker-compose logs

# Restart all services
docker-compose down && docker-compose up -d
```

### Ollama model issues
```bash
# Pull the model manually
docker-compose exec ollama ollama pull llama3.1:8b

# Check available models
curl http://localhost:11434/api/tags
```

### Database connection issues
```bash
# Check database status
docker-compose exec db pg_isready -U luthien -d luthien_control

# Reset database
docker-compose down -v && docker-compose up -d
```

### API key issues
```bash
# Check your .env file
cat .env

# Restart proxy after changing keys
docker-compose restart litellm-proxy
```

## Getting Help

- Check the [main README](../README.md) for architecture details
- View service logs: `docker-compose logs <service-name>`
- Monitor services: `./scripts/monitor_services.sh`
- Test the setup: `uv run python scripts/test_proxy.py`

Happy building! 🚀
