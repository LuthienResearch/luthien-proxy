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

# Copy proxy source and install it
COPY . /opt/luthien-proxy
WORKDIR /opt/luthien-proxy

# Install luthien-proxy into the managed venv at ~/.luthien/venv
# (same location luthien onboard creates — so it skips re-installing from GitHub)
RUN mkdir -p /root/.luthien/luthien-proxy/config && \
    uv venv /root/.luthien/venv --python 3.13 && \
    uv pip install --python /root/.luthien/venv/bin/python /opt/luthien-proxy

# Copy migrations so the schema can be found at runtime
RUN cp -r /opt/luthien-proxy/migrations /root/.luthien/luthien-proxy/migrations

# Pre-compile bytecode to avoid slow .pyc writes at runtime
RUN /root/.luthien/venv/bin/python -m compileall -q \
    /root/.luthien/venv/lib/python3.13/site-packages/luthien_proxy/

# Stamp the venv so ensure_gateway_venv() treats it as up-to-date and
# doesn't overwrite it with the GitHub main version (which lacks our changes).
# ensure_gateway_venv checks venv_python.exists() — if it does, it runs
# `uv pip install --upgrade` from GitHub. We block that by making uv
# a wrapper that skips the GitHub install.
# Simpler: just pin the local source as editable so --upgrade is a no-op.
# Actually simplest: patch ensure_gateway_venv to install from /opt/luthien-proxy.
# But we don't want to modify CLI code for a test container.
#
# Solution: wrap uv so `uv pip install --upgrade git+https://...` is a no-op
RUN mv /usr/local/bin/uv /usr/local/bin/uv-real && \
    printf '#!/bin/bash\n\
# Skip GitHub installs of luthien-proxy (already installed from local source)\n\
for arg in "$@"; do\n\
  if [[ "$arg" == *"github.com/LuthienResearch/luthien-proxy"* ]]; then\n\
    echo "Skipping GitHub install (using pre-installed local build)" >&2\n\
    exit 0\n\
  fi\n\
done\n\
exec /usr/local/bin/uv-real "$@"\n' > /usr/local/bin/uv && \
    chmod +x /usr/local/bin/uv

# Install luthien-cli
RUN uv-real pip install --system /opt/luthien-proxy/src/luthien_cli

WORKDIR /root
CMD ["/bin/bash"]
