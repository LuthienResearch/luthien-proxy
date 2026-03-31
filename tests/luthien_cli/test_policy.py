"""Tests for luthien policy command."""

from unittest.mock import patch

from click.testing import CliRunner

from luthien_cli.gateway_client import GatewayError
from luthien_cli.main import cli

SAMPLE_POLICIES = [
    {
        "name": "NoOpPolicy",
        "class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
        "description": "Pass-through policy that makes no modifications.",
        "config_schema": {},
        "example_config": {},
    },
    {
        "name": "DeslopifyPolicy",
        "class_ref": "luthien_proxy.policies.deslopify_policy:DeslopifyPolicy",
        "description": "Strips sycophantic openers, hollow closers, and inline filler.",
        "config_schema": {},
        "example_config": {},
    },
    {
        "name": "StringReplacementPolicy",
        "class_ref": "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
        "description": "Replaces text patterns in responses using regex.",
        "config_schema": {
            "replacements": {
                "type": "array",
                "required": True,
            }
        },
        "example_config": {"replacements": [{"pattern": "foo", "replacement": "bar"}]},
    },
    {
        "name": "PreferUvPolicy",
        "class_ref": "luthien_proxy.policies.presets.prefer_uv:PreferUvPolicy",
        "description": "Replaces pip commands with uv equivalents.",
        "config_schema": {},
        "example_config": {},
    },
]

ACTIVE_POLICY = {
    "policy": "NoOpPolicy",
    "class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
    "enabled_at": "2026-03-31T10:00:00",
    "enabled_by": "api",
    "config": {},
}


def _patch_client():
    return patch("luthien_cli.commands.policy._make_client")


class TestPolicyBare:
    """luthien policy (no subcommand) — shows standard Click help."""

    def test_shows_help_text(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["policy"])
        assert result.exit_code == 0
        assert "Commands:" in result.output
        assert "current" in result.output
        assert "list" in result.output
        assert "set" in result.output
        assert "show" in result.output


class TestPolicyCurrent:
    def test_current_subcommand(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "current"])
        assert result.exit_code == 0
        assert "NoOpPolicy" in result.output
        assert "noop_policy" in result.output

    def test_current_shows_enabled_info(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "current"])
        assert result.exit_code == 0
        assert "api" in result.output

    def test_current_shows_config(self):
        runner = CliRunner()
        policy_with_config = {**ACTIVE_POLICY, "config": {"key": "value"}}
        with _patch_client() as mock:
            mock.return_value.get_current_policy.return_value = policy_with_config
            result = runner.invoke(cli, ["policy", "current"])
        assert result.exit_code == 0
        assert "key" in result.output

    def test_current_gateway_error(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.get_current_policy.side_effect = GatewayError("Cannot connect")
            result = runner.invoke(cli, ["policy", "current"])
        assert result.exit_code != 0
        assert "Cannot connect" in result.output


class TestPolicyList:
    def test_list_shows_all_policies(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "list"])
        assert result.exit_code == 0
        assert "NoOpPolicy" in result.output
        assert "DeslopifyPolicy" in result.output
        assert "StringReplacementPolicy" in result.output

    def test_list_separates_presets(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "list"])
        assert result.exit_code == 0
        assert "Policies" in result.output
        assert "Presets" in result.output
        assert "PreferUvPolicy" in result.output

    def test_list_marks_active_policy(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "list"])
        assert result.exit_code == 0
        assert ">" in result.output

    def test_list_shows_counts(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "list"])
        assert "3 policies" in result.output
        assert "1 presets" in result.output

    def test_list_verbose_shows_class_refs(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "list", "-v"])
        assert result.exit_code == 0
        assert "luthien_proxy.policies.noop_policy:NoOpPolicy" in result.output

    def test_list_verbose_shows_config_params(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "list", "-v"])
        assert "replacements" in result.output

    def test_list_gateway_error(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.side_effect = GatewayError("Cannot connect")
            result = runner.invoke(cli, ["policy", "list"])
        assert result.exit_code != 0


class TestPolicyShow:
    def test_show_by_short_name(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            result = runner.invoke(cli, ["policy", "show", "DeslopifyPolicy"])
        assert result.exit_code == 0
        assert "DeslopifyPolicy" in result.output
        assert "deslopify_policy" in result.output

    def test_show_by_full_class_ref(self):
        runner = CliRunner()
        ref = "luthien_proxy.policies.noop_policy:NoOpPolicy"
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            result = runner.invoke(cli, ["policy", "show", ref])
        assert result.exit_code == 0
        assert "NoOpPolicy" in result.output

    def test_show_case_insensitive(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            result = runner.invoke(cli, ["policy", "show", "nooppolicy"])
        assert result.exit_code == 0
        assert "NoOpPolicy" in result.output

    def test_show_with_config_schema(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            result = runner.invoke(cli, ["policy", "show", "StringReplacementPolicy"])
        assert result.exit_code == 0
        assert "replacements" in result.output
        assert "required" in result.output

    def test_show_includes_activation_hint_for_inactive(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "show", "DeslopifyPolicy"])
        assert result.exit_code == 0
        assert "luthien policy set DeslopifyPolicy" in result.output

    def test_show_active_has_active_label(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "show", "NoOpPolicy"])
        assert result.exit_code == 0
        assert "active" in result.output

    def test_show_active_no_activation_hint(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "show", "NoOpPolicy"])
        assert result.exit_code == 0
        assert "Activate with" not in result.output

    def test_show_defaults_to_active(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "show"])
        assert result.exit_code == 0
        assert "NoOpPolicy" in result.output
        assert "active" in result.output

    def test_show_unknown_policy(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            result = runner.invoke(cli, ["policy", "show", "NonExistentPolicy"])
        assert result.exit_code != 0
        assert "No policy found" in result.output


class TestPolicySet:
    def test_set_by_short_name(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.set_policy.return_value = {"success": True}
            result = runner.invoke(cli, ["policy", "set", "DeslopifyPolicy"])
        assert result.exit_code == 0
        assert "DeslopifyPolicy" in result.output
        mock.return_value.set_policy.assert_called_once_with(
            "luthien_proxy.policies.deslopify_policy:DeslopifyPolicy", {}
        )

    def test_set_by_full_ref(self):
        runner = CliRunner()
        ref = "luthien_proxy.policies.noop_policy:NoOpPolicy"
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.set_policy.return_value = {"success": True}
            result = runner.invoke(cli, ["policy", "set", ref])
        assert result.exit_code == 0
        mock.return_value.set_policy.assert_called_once_with(ref, {})

    def test_set_with_config(self):
        runner = CliRunner()
        config_json = '{"replacements": [{"pattern": "foo", "replacement": "bar"}]}'
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.set_policy.return_value = {"success": True}
            result = runner.invoke(
                cli, ["policy", "set", "StringReplacementPolicy", "--config", config_json]
            )
        assert result.exit_code == 0
        call_args = mock.return_value.set_policy.call_args
        assert call_args[0][1]["replacements"][0]["pattern"] == "foo"

    def test_set_invalid_json_config(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            result = runner.invoke(cli, ["policy", "set", "NoOpPolicy", "--config", "{bad"])
        assert result.exit_code != 0
        assert "Invalid JSON" in result.output

    def test_set_unknown_policy(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            result = runner.invoke(cli, ["policy", "set", "FakePolicy"])
        assert result.exit_code != 0
        assert "No policy found" in result.output

    def test_set_failure_shows_troubleshooting(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.set_policy.return_value = {
                "success": False,
                "error": "Validation error",
                "troubleshooting": ["replacements is required"],
            }
            result = runner.invoke(cli, ["policy", "set", "NoOpPolicy"])
        assert result.exit_code != 0
        assert "Validation error" in result.output
        assert "replacements is required" in result.output

    def test_set_gateway_error(self):
        runner = CliRunner()
        with _patch_client() as mock:
            mock.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock.return_value.set_policy.side_effect = GatewayError("Cannot connect")
            result = runner.invoke(cli, ["policy", "set", "NoOpPolicy"])
        assert result.exit_code != 0

    def test_set_interactive_picker(self):
        """Interactive picker selects correct policy via _interactive_pick mock."""
        runner = CliRunner()
        with (
            _patch_client() as mock_client,
            patch("luthien_cli.commands.policy._interactive_pick", return_value=1) as mock_pick,
            patch("luthien_cli.commands.policy.sys") as mock_sys,
        ):
            mock_sys.stdin.isatty.return_value = True
            mock_client.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock_client.return_value.get_current_policy.return_value = ACTIVE_POLICY
            mock_client.return_value.set_policy.return_value = {"success": True}
            result = runner.invoke(cli, ["policy", "set"])
        assert result.exit_code == 0
        assert "DeslopifyPolicy" in result.output
        mock_pick.assert_called_once()
        mock_client.return_value.set_policy.assert_called_once_with(
            "luthien_proxy.policies.deslopify_policy:DeslopifyPolicy", {}
        )

    def test_set_interactive_cancelled(self):
        """Interactive picker returns None when user cancels."""
        runner = CliRunner()
        with (
            _patch_client() as mock_client,
            patch("luthien_cli.commands.policy._interactive_pick", return_value=None),
            patch("luthien_cli.commands.policy.sys") as mock_sys,
        ):
            mock_sys.stdin.isatty.return_value = True
            mock_client.return_value.list_policies.return_value = SAMPLE_POLICIES
            mock_client.return_value.get_current_policy.return_value = ACTIVE_POLICY
            result = runner.invoke(cli, ["policy", "set"])
        assert result.exit_code == 0
        assert "Cancelled" in result.output

    def test_set_no_name_non_interactive(self):
        """Non-interactive stdin without a name shows usage hint."""
        runner = CliRunner()
        with (
            _patch_client() as mock_client,
            patch("luthien_cli.commands.policy.sys") as mock_sys,
        ):
            mock_sys.stdin.isatty.return_value = False
            mock_client.return_value.list_policies.return_value = SAMPLE_POLICIES
            result = runner.invoke(cli, ["policy", "set"])
        assert result.exit_code != 0
        assert "not a terminal" in result.output


class TestResolveClassRef:
    def test_exact_class_ref(self):
        from luthien_cli.commands.policy import _resolve_class_ref

        ref = "luthien_proxy.policies.noop_policy:NoOpPolicy"
        assert _resolve_class_ref(ref, SAMPLE_POLICIES) == ref

    def test_short_name(self):
        from luthien_cli.commands.policy import _resolve_class_ref

        result = _resolve_class_ref("NoOpPolicy", SAMPLE_POLICIES)
        assert result == "luthien_proxy.policies.noop_policy:NoOpPolicy"

    def test_case_insensitive(self):
        from luthien_cli.commands.policy import _resolve_class_ref

        result = _resolve_class_ref("nooppolicy", SAMPLE_POLICIES)
        assert result == "luthien_proxy.policies.noop_policy:NoOpPolicy"

    def test_no_match(self):
        from luthien_cli.commands.policy import _resolve_class_ref

        assert _resolve_class_ref("FakePolicy", SAMPLE_POLICIES) is None


class TestHelpers:
    def test_is_preset(self):
        from luthien_cli.commands.policy import _is_preset

        assert _is_preset(SAMPLE_POLICIES[3]) is True  # PreferUvPolicy
        assert _is_preset(SAMPLE_POLICIES[0]) is False  # NoOpPolicy

    def test_short_name(self):
        from luthien_cli.commands.policy import _short_name

        assert _short_name("module.path:ClassName") == "ClassName"
        assert _short_name("NoColon") == "NoColon"

    def test_truncate(self):
        from luthien_cli.commands.policy import _truncate

        assert _truncate("short", 40) == "short"
        assert len(_truncate("a" * 100, 40)) == 40
