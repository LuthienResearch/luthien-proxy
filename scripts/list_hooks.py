#!/usr/bin/env python3

"""
List all hook functions available on LiteLLM's CustomLogger class.
Also prints which of those our callback implements.
"""

import inspect
import sys


def main() -> int:
    try:
        from litellm.integrations.custom_logger import CustomLogger
    except Exception as e:
        print(f"Failed to import CustomLogger: {e}")
        return 1

    names = []
    for name, val in inspect.getmembers(CustomLogger, predicate=inspect.isfunction):
        if any(k in name for k in ("hook", "log_", "stream")):
            names.append(name)

    print("CustomLogger hook-like methods:")
    for n in sorted(names):
        print(f" - {n}")

    try:
        import importlib

        cb_mod = importlib.import_module("litellm_callback")
        cb = getattr(cb_mod, "luthien_callback", None)
        if cb is None:
            print("\nOur callback (luthien_callback) not found.")
            return 0
        impl = []
        for n in sorted(names):
            if hasattr(cb, n):
                impl.append(n)
        print("\nImplemented by our callback:")
        for n in impl:
            print(f" ✓ {n}")
    except Exception as e:
        print(f"\nFailed to inspect our callback: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
