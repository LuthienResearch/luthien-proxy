---
category: Fixes
pr: 804
---

**Anthropic streaming: re-inject keepalive pings so long generations don't idle out**: The Anthropic SDK's typed stream (`messages.create(stream=True)`) drops the wire `ping` events Anthropic emits during long generations. A model that stays silent before emitting content (e.g. a slow or classifier-gated response) therefore produced no bytes on the proxy→client connection for the entire pre-content phase, so any intermediary with an idle timeout (load balancer, reverse proxy) would cut the healthy stream mid-flight. The gateway now re-emits an Anthropic-style `ping` whenever the upstream is idle longer than `STREAM_KEEPALIVE_SECONDS` (15s), matching the keepalive behavior a direct Anthropic connection relies on.
