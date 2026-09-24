import pytest

from app.pipeline.config import RepoConfig, parse_repo_config

GOOD = """
strictness: high
ignore: ["docs/*", "*.generated.ts"]
rules:
  - Flag any raw SQL string building
  - "  "
max_comments: 10
"""


def test_defaults_when_missing_or_blank():
    for text in (None, "", "   \n", "# only a comment"):
        cfg, warn = parse_repo_config(text)
        assert cfg == RepoConfig() and warn is None


def test_valid_config():
    cfg, warn = parse_repo_config(GOOD)
    assert warn is None
    assert cfg.strictness == "high" and cfg.max_comments == 10
    assert cfg.ignore == ["docs/*", "*.generated.ts"]
    assert cfg.rules == ["Flag any raw SQL string building"]  # blank rule dropped


@pytest.mark.parametrize("strictness,expected", [("low", 0.8), ("medium", 0.6), ("high", 0.4)])
def test_strictness_sets_the_confidence_threshold(strictness, expected):
    assert RepoConfig(strictness=strictness).min_confidence == expected


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("strictness: [unclosed", "not valid YAML"),
        ("- just\n- a list", "must be a mapping"),
        ("strictness: extreme", "strictness"),
        ("max_comments: 0", "max_comments"),
        ("ignore: notalist", "ignore"),
    ],
)
def test_broken_config_falls_back_to_defaults_with_a_warning(text, fragment):
    cfg, warn = parse_repo_config(text)
    assert cfg == RepoConfig() and fragment in warn


def test_long_rules_are_truncated():
    cfg, _ = parse_repo_config("rules: ['" + "x" * 500 + "']")
    assert len(cfg.rules[0]) == 300


def test_yaml_cannot_execute_code():
    cfg, warn = parse_repo_config("strictness: !!python/object/apply:os.system ['echo hi']")
    assert cfg == RepoConfig() and warn
