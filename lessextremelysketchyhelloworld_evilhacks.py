#!/usr/bin/env python3
"""Moderately sketchy Hello World.

Still completely harmless, just a bit less theatrical.
"""

import base64

# Slightly obfuscated greeting
message = base64.b64decode(b"SGVsbG8sIFdvcmxkIQ==").decode()


def main() -> None:
    """Print the greeting."""
    print(message)


if __name__ == "__main__":
    main()
