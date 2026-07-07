"""Allow `python -m luthien_proxy.policy_generation <path>` as a shorthand."""

from luthien_proxy.policy_generation.claude_md import main

if __name__ == "__main__":
    raise SystemExit(main())
