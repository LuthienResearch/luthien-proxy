"""Tests for scripts/compile_changelog.py."""

# Import the module under test by path
import importlib.util
import textwrap
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "compile_changelog",
    Path(__file__).resolve().parents[3] / "scripts" / "compile_changelog.py",
)
assert _spec and _spec.loader
compile_changelog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compile_changelog)

parse_fragment = compile_changelog.parse_fragment
collect_fragments = compile_changelog.collect_fragments
build_section = compile_changelog.build_section
insert_into_changelog = compile_changelog.insert_into_changelog
SKIP_FILES = compile_changelog.SKIP_FILES


@pytest.fixture()
def fragments_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "changelog.d"
    d.mkdir()
    monkeypatch.setattr(compile_changelog, "FRAGMENTS_DIR", d)
    return d


@pytest.fixture()
def changelog_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "CHANGELOG.md"
    p.write_text(
        textwrap.dedent("""\
        # CHANGELOG

        ## Unreleased | TBA

        ## 0.0.2 | 2025-11-07

        - Old entry
        """)
    )
    monkeypatch.setattr(compile_changelog, "CHANGELOG_PATH", p)
    return p


class TestParseFragment:
    def test_valid_fragment(self, tmp_path: Path) -> None:
        f = tmp_path / "feat.md"
        f.write_text(
            textwrap.dedent("""\
            ---
            category: Features
            pr: 42
            ---

            **Cool thing**: it works
            """)
        )
        result = parse_fragment(f)
        assert result["category"] == "Features"
        assert result["pr"] == "42"
        assert "Cool thing" in result["body"]

    def test_no_pr(self, tmp_path: Path) -> None:
        f = tmp_path / "fix.md"
        f.write_text(
            textwrap.dedent("""\
            ---
            category: Fixes
            ---

            **Bug fix**: fixed it
            """)
        )
        result = parse_fragment(f)
        assert result["pr"] == ""

    def test_bad_category_exits(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text(
            textwrap.dedent("""\
            ---
            category: Nonsense
            ---

            stuff
            """)
        )
        with pytest.raises(SystemExit):
            parse_fragment(f)

    def test_missing_frontmatter_exits(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("Just some text with no frontmatter")
        with pytest.raises(SystemExit):
            parse_fragment(f)


class TestCollectFragments:
    def test_empty_dir(self, fragments_dir: Path) -> None:
        grouped = collect_fragments()
        assert all(len(v) == 0 for v in grouped.values())

    def test_groups_by_category(self, fragments_dir: Path) -> None:
        (fragments_dir / "feat.md").write_text("---\ncategory: Features\npr: 1\n---\n\n**A**: desc")
        (fragments_dir / "fix.md").write_text("---\ncategory: Fixes\n---\n\n**B**: desc")
        grouped = collect_fragments()
        assert len(grouped["Features"]) == 1
        assert len(grouped["Fixes"]) == 1
        assert "(#1)" in grouped["Features"][0]

    def test_skips_readme_and_gitkeep(self, fragments_dir: Path) -> None:
        (fragments_dir / "README.md").write_text("docs")
        (fragments_dir / ".gitkeep").write_text("")
        grouped = collect_fragments()
        assert all(len(v) == 0 for v in grouped.values())

    def test_pr_not_duplicated(self, fragments_dir: Path) -> None:
        (fragments_dir / "f.md").write_text("---\ncategory: Features\npr: 99\n---\n\n**Thing** (#99)")
        grouped = collect_fragments()
        # Should not have doubled (#99)
        assert grouped["Features"][0].count("#99") == 1


class TestBuildSection:
    def test_builds_markdown(self) -> None:
        grouped = {
            "Features": ["**A**: works"],
            "Fixes": ["**B**: fixed"],
            "Refactors": [],
            "Chores & Docs": [],
        }
        section = build_section(grouped)
        assert "### Features" in section
        assert "### Fixes" in section
        assert "### Refactors" not in section
        assert "- **A**: works" in section

    def test_multiline_entries(self) -> None:
        grouped = {
            "Features": ["**A**: works\n  - sub-bullet"],
            "Fixes": [],
            "Refactors": [],
            "Chores & Docs": [],
        }
        section = build_section(grouped)
        assert "- **A**: works" in section
        assert "  - sub-bullet" in section


class TestInsertIntoChangelog:
    def test_inserts_under_unreleased(self, changelog_file: Path) -> None:
        insert_into_changelog("### Features\n\n- **New**: thing\n\n", dry_run=False)
        content = changelog_file.read_text()
        assert "- **New**: thing" in content
        # Original content preserved
        assert "## 0.0.2" in content
        assert "- Old entry" in content
        # New content comes before old release
        features_pos = content.index("### Features")
        old_release_pos = content.index("## 0.0.2")
        assert features_pos < old_release_pos
