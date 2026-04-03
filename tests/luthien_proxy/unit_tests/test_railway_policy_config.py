# ABOUTME: Tests that config/railway_policy_config.yaml is valid and references real policy classes
# ABOUTME: Ensures the one-click Railway deploy won't fail due to policy config errors

"""Tests for Railway default policy configuration."""

import importlib
from pathlib import Path

import yaml

RAILWAY_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "railway_policy_config.yaml"


def _import_class(class_path: str):
    """Import a class from a 'module.path:ClassName' string."""
    module_path, class_name = class_path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class TestRailwayPolicyConfig:
    """Validate the Railway default policy configuration."""

    def test_config_file_exists(self):
        assert RAILWAY_CONFIG_PATH.exists(), f"Railway policy config not found at {RAILWAY_CONFIG_PATH}"

    def test_config_is_valid_yaml(self):
        config = yaml.safe_load(RAILWAY_CONFIG_PATH.read_text())
        assert "policy" in config
        assert "class" in config["policy"]

    def test_top_level_policy_class_importable(self):
        config = yaml.safe_load(RAILWAY_CONFIG_PATH.read_text())
        cls = _import_class(config["policy"]["class"])
        assert cls is not None

    def test_sub_policies_importable(self):
        config = yaml.safe_load(RAILWAY_CONFIG_PATH.read_text())
        sub_policies = config["policy"].get("config", {}).get("policies", [])
        assert len(sub_policies) >= 2, "Expected at least 2 sub-policies (logging + rules)"

        for i, sub in enumerate(sub_policies):
            cls = _import_class(sub["class"])
            assert cls is not None, f"Sub-policy {i} class not importable: {sub['class']}"

    def test_uses_multi_serial_policy(self):
        config = yaml.safe_load(RAILWAY_CONFIG_PATH.read_text())
        assert "MultiSerialPolicy" in config["policy"]["class"]

    def test_includes_debug_logging(self):
        config = yaml.safe_load(RAILWAY_CONFIG_PATH.read_text())
        sub_policies = config["policy"]["config"]["policies"]
        class_names = [p["class"] for p in sub_policies]
        assert any("DebugLoggingPolicy" in c for c in class_names)

    def test_includes_simple_llm_policy(self):
        config = yaml.safe_load(RAILWAY_CONFIG_PATH.read_text())
        sub_policies = config["policy"]["config"]["policies"]
        class_names = [p["class"] for p in sub_policies]
        assert any("SimpleLLMPolicy" in c for c in class_names)

    def test_simple_llm_policy_has_instructions(self):
        config = yaml.safe_load(RAILWAY_CONFIG_PATH.read_text())
        sub_policies = config["policy"]["config"]["policies"]
        llm_policies = [p for p in sub_policies if "SimpleLLMPolicy" in p["class"]]
        assert len(llm_policies) == 1

        llm_config = llm_policies[0]["config"]["config"]
        assert "instructions" in llm_config
        assert len(llm_config["instructions"]) > 50, "Instructions should be substantive"

    def test_simple_llm_policy_on_error_is_pass(self):
        config = yaml.safe_load(RAILWAY_CONFIG_PATH.read_text())
        sub_policies = config["policy"]["config"]["policies"]
        llm_policies = [p for p in sub_policies if "SimpleLLMPolicy" in p["class"]]

        llm_config = llm_policies[0]["config"]["config"]
        assert llm_config["on_error"] == "pass"
