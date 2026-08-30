from __future__ import annotations

from pathlib import Path

import pytest

from mcp_email_server.imap_keywords import ImapKeywordConfigurationError, ImapKeywordRegistry


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_minimal_tag_defaults_are_safe(tmp_path: Path) -> None:
    registry = ImapKeywordRegistry.load(
        _write(
            tmp_path / "imap_keywords.toml",
            """
[accounts.alonso]
[[accounts.alonso.tags]]
name = "todo"
keyword = "$label4"
""",
        )
    )

    assert registry.tags_for("alonso")[0].model_dump() == {
        "name": "todo",
        "keyword": "$label4",
        "description": "",
        "writable": False,
    }
    assert registry.resolve("alonso", ("todo",)) == ("$label4",)
    with pytest.raises(PermissionError, match="not writable"):
        registry.resolve("alonso", ("todo",), require_writable=True)


def test_explicit_writable_tag_resolves_name_and_keyword(tmp_path: Path) -> None:
    registry = ImapKeywordRegistry.load(
        _write(
            tmp_path / "imap_keywords.toml",
            """
[accounts.alonso]
[[accounts.alonso.tags]]
name = "todo"
keyword = "$label4"
description = "Messages requiring an action"
writable = true
""",
        )
    )

    assert registry.resolve("alonso", ("todo", "$label4"), require_writable=True) == ("$label4",)
    assert registry.semantic_names("alonso", ["unknown", "$label4"]) == ["todo"]
    assert registry.writable_keywords("alonso") == ("$label4",)


@pytest.mark.parametrize(
    "body",
    [
        """
[accounts.alonso]
[[accounts.alonso.tags]]
name = "todo"
keyword = "$label4"
[[accounts.alonso.tags]]
name = "todo"
keyword = "$label5"
""",
        """
[accounts.alonso]
[[accounts.alonso.tags]]
name = "todo"
keyword = "$label4"
[[accounts.alonso.tags]]
name = "important"
keyword = "$label4"
""",
        """
[accounts.alonso]
[[accounts.alonso.tags]]
name = "seen"
keyword = "\\Seen"
""",
        """
[accounts.alonso]
[[accounts.alonso.tags]]
name = "bad"
keyword = "not valid"
""",
        """
[accounts.alonso]
[[accounts.alonso.tags]]
name = "todo"
keyword = "$label4"
writable = "yes"
""",
    ],
)
def test_invalid_keyword_configuration_fails_closed(tmp_path: Path, body: str) -> None:
    with pytest.raises(ImapKeywordConfigurationError, match="invalid"):
        ImapKeywordRegistry.load(_write(tmp_path / "imap_keywords.toml", body))


def test_absent_file_is_empty_and_unknown_tag_is_controlled(tmp_path: Path) -> None:
    registry = ImapKeywordRegistry.load(tmp_path / "missing.toml")

    assert registry.tags_for("alonso") == ()
    with pytest.raises(ValueError, match="Unknown configured email tag"):
        registry.resolve("alonso", ("todo",))
