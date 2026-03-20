FROM python:3.13-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js (for Claude Code)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Install Claude Code
RUN npm install -g @anthropic-ai/claude-code

# Pre-stage the repo so `luthien hackathon` finds it already cloned.
# This avoids hitting GitHub from the container and tests against
# the current branch rather than whatever's on main.
COPY . /root/luthien-proxy
WORKDIR /root/luthien-proxy

# Initialize a git repo so hatch-vcs can derive a version for luthien-cli.
# COPY doesn't include .git/, so builds from the Docker context have no history.
RUN git init && git config user.email "test@test" && git config user.name "test" \
    && git add -A && git commit -m "init" && git tag cli-v0.0.0-dev

# Pre-install deps so the hackathon command skips the slow uv sync
RUN uv sync --dev

# Install luthien-cli from current source
RUN SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0.dev0 \
    uv pip install --system /root/luthien-proxy/src/luthien_cli

WORKDIR /root
CMD ["/bin/bash"]
