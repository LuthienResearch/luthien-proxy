# Publishing Policy Events to Activity Stream (V1)

> **Note**: This guide applies to V1 policies using the LiteLLM proxy + control plane architecture.
> For V2 policies, see the policy implementation examples in `src/luthien_proxy/v2/policies/`.

## Overview

V1 policies can publish events to the global activity stream to make their decisions and actions visible in real-time monitoring at `/ui/activity/live`.

## Usage

Import the helper function:

```python
from luthien_proxy.control_plane.conversation.policy_events import (
    publish_policy_event_to_activity_stream
)
```

Publish an event when your policy takes an action:

```python
async def async_post_call_success_hook(
    self,
    data: JSONObject,
    user_api_key_dict: JSONObject | None,
    response: JSONObject,
) -> JSONObject:
    # ... your policy logic ...

    # Publish to activity stream
    if self._redis and call_id:
        await publish_policy_event_to_activity_stream(
            self._redis,
            call_id=call_id,
            policy_class=self.__class__.__name__,
            event_type="content_filtered",
            metadata={
                "action": "blocked_sensitive_content",
                "reason": "PII detected in response",
                "confidence": 0.95,
            },
        )

    return modified_response
```

## Parameters

- `redis`: Redis client instance (available via `self._redis` in policies)
- `call_id`: LiteLLM call ID
- `policy_class`: Policy class name (use `self.__class__.__name__`)
- `event_type`: Type of policy event (e.g., "content_filtered", "rate_limited", "approved")
- `policy_config`: Optional policy configuration
- `metadata`: Optional dict with event-specific data

## Event Display

Policy events appear in the activity stream UI with:
- Orange badge labeled "policy action"
- Summary showing policy name and action
- Full metadata in expandable payload

## Example: Rate Limiting Policy

```python
class RateLimitPolicy(LuthienPolicy):
    async def async_pre_call_hook(
        self,
        data: JSONObject,
        user_api_key_dict: JSONObject | None,
    ) -> JSONObject:
        user_id = self._extract_user_id(user_api_key_dict)
        call_id = data.get("litellm_call_id")

        if self._is_rate_limited(user_id):
            # Publish rate limit event
            if self._redis and call_id:
                await publish_policy_event_to_activity_stream(
                    self._redis,
                    call_id=str(call_id),
                    policy_class=self.__class__.__name__,
                    event_type="rate_limited",
                    metadata={
                        "action": "request_rejected",
                        "reason": f"User {user_id} exceeded rate limit",
                        "limit": self.max_requests_per_minute,
                        "current_rate": self._get_current_rate(user_id),
                    },
                )
            raise RateLimitError(f"Rate limit exceeded for user {user_id}")

        # Publish approval event
        if self._redis and call_id:
            await publish_policy_event_to_activity_stream(
                self._redis,
                call_id=str(call_id),
                policy_class=self.__class__.__name__,
                event_type="rate_check_passed",
                metadata={
                    "action": "request_approved",
                    "current_rate": self._get_current_rate(user_id),
                    "limit": self.max_requests_per_minute,
                },
            )

        return data
```

## Best Practices

1. **Publish significant decisions**: Rate limits, content filtering, approval/rejection
2. **Include actionable metadata**: Why the decision was made, confidence scores, affected content
3. **Use consistent event_type names**: Stick to a naming convention across your policies
4. **Keep summaries concise**: The summary appears in the timeline, full details go in metadata
5. **Don't over-publish**: Avoid publishing on every single request unless necessary for debugging

## Accessing Redis Client

To publish events, your policy needs access to the Redis client. This is typically injected by the control plane:

```python
def set_redis_client(self, redis: redis_client.RedisClient) -> None:
    """Inject Redis client for publishing policy events."""
    self._redis = redis
```

Check if Redis is available before publishing:

```python
if hasattr(self, '_redis') and self._redis:
    await publish_policy_event_to_activity_stream(...)
```
