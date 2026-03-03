# Sandbox for overseer e2e tests — runs Claude Code sessions in isolation
FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git python3 python3-venv build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox
WORKDIR /work

# Container stays alive; overseer does `docker exec` into it
ENTRYPOINT ["sleep", "infinity"]
