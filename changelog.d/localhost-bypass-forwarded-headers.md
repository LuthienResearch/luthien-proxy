---
category: Fixes
---

**Security: localhost auth bypass refuses proxied requests**: the localhost auth bypass no longer applies to requests that carry reverse-proxy forwarding headers (`X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `X-Forwarded-Host`, `X-Forwarded-Proto`), even when the TCP source address is loopback. A same-host reverse proxy (Caddy, nginx, Traefik) previously made every external request look like 127.0.0.1 and exposed the admin/history/debug surface unauthenticated. Direct loopback requests (local curl, dockerless dev, `luthien` CLI) still bypass as before. The gateway also logs a startup warning whenever the bypass is enabled.
