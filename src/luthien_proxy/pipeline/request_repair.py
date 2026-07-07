"""Automatic repair of known-fixable 400 request errors.

The Anthropic API rejects requests containing unrecognized fields with a
400 whose message names the offending field, e.g.
``"tools.0.bogus: Extra inputs are not permitted"``. When the field can be
located in the request payload, the pipeline strips it and retries the
backend call exactly once.

Repairs are never silent: the caller (``_AnthropicPolicyIO`` in
``pipeline/anthropic_processor.py``) emits a ``pipeline.retry_with_fix``
observability event and logs a warning before retrying. This module is pure:
it only computes the repaired request, it performs no I/O.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import cast

from luthien_proxy.llm.types.anthropic import AnthropicRequest

# Matches "<dotted.field.path>: Extra inputs are not permitted" anywhere in the
# upstream message. Path segments are dict keys or list indices separated by
# dots. The field path may be bare or wrapped in quotes/backticks.
_EXTRA_FIELD_PATTERN = re.compile(
    r"(?:^|[\s'\"`(])([A-Za-z0-9_][A-Za-z0-9_.\-]*)['\"`]?: Extra inputs are not permitted"
)

# Fields the pipeline itself relies on; never auto-remove these even if an
# upstream message implicates them (which would indicate a deeper problem
# that field-stripping cannot fix).
_PROTECTED_TOP_LEVEL_FIELDS = frozenset({"model", "messages", "max_tokens", "stream"})


@dataclass(frozen=True)
class RequestFix:
    """A repaired request plus a description of what changed.

    Attributes:
        request: Deep copy of the original request with the fix applied.
        removed_field: Dotted path of the field that was removed.
        description: Human-readable summary of the repair (for logs/events).
    """

    request: AnthropicRequest
    removed_field: str
    description: str


def attempt_request_fix(request: AnthropicRequest, error_message: str) -> RequestFix | None:
    """Try to repair a request rejected with a known-fixable 400 error.

    Currently handles one pattern: an unrecognized extra field
    ("<path>: Extra inputs are not permitted"), which is removed from a deep
    copy of the request. The original request is never mutated.

    Args:
        request: The request payload that the upstream API rejected.
        error_message: Raw upstream 400 error message.

    Returns:
        A RequestFix when the offending field was located and removed, or
        None when the error does not match a fixable pattern, the field path
        cannot be resolved in the payload, or the field is load-bearing for
        the pipeline (model, messages, max_tokens, stream).
    """
    match = _EXTRA_FIELD_PATTERN.search(error_message)
    if match is None:
        return None

    field_path = match.group(1)
    segments = field_path.split(".")
    if len(segments) == 1 and segments[0] in _PROTECTED_TOP_LEVEL_FIELDS:
        return None

    repaired: dict = copy.deepcopy(dict(request))
    container: object = repaired
    for segment in segments[:-1]:
        if isinstance(container, dict) and segment in container:
            container = container[segment]
        elif isinstance(container, list) and segment.isdigit() and int(segment) < len(container):
            container = container[int(segment)]
        else:
            return None

    leaf = segments[-1]
    if not isinstance(container, dict) or leaf not in container:
        return None
    del container[leaf]

    return RequestFix(
        request=cast(AnthropicRequest, repaired),
        removed_field=field_path,
        description=f"removed field '{field_path}' rejected by the upstream API as an extra input",
    )


__all__ = ["RequestFix", "attempt_request_fix"]
