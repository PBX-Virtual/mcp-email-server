from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mcp_email_server.application.limits import APPLICATION_LIMITS, validate_controlled_string

DEFAULT_IMAP_KEYWORDS_PATH = "~/.config/mcp-email-server/imap_keywords.toml"


class ImapKeywordConfigurationError(ValueError):
    """The process-scoped semantic IMAP keyword file is invalid."""


def _is_imap_keyword(value: str) -> bool:
    # RFC 3501 atom-specials plus controls, space, and non-ASCII are forbidden.
    atom_specials = frozenset('(){%*]\\"')
    return bool(value) and all(0x21 <= ord(character) <= 0x7E and character not in atom_specials for character in value)


class ImapKeywordTag(BaseModel):
    """One semantic name mapped to one provider keyword."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    keyword: str
    description: str = ""
    writable: bool = Field(default=False, strict=True)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return validate_controlled_string(
            value,
            field_name="tag name",
            maximum_bytes=APPLICATION_LIMITS.flag_bytes,
        )

    @field_validator("keyword")
    @classmethod
    def _validate_keyword(cls, value: str) -> str:
        validate_controlled_string(
            value,
            field_name="tag keyword",
            maximum_bytes=APPLICATION_LIMITS.flag_bytes,
        )
        if value.startswith("\\") or not _is_imap_keyword(value):
            raise ValueError("tag keyword must be a non-system IMAP keyword atom")
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return validate_controlled_string(
            value,
            field_name="tag description",
            maximum_bytes=APPLICATION_LIMITS.account_description_bytes,
            allow_empty=True,
        )


class ImapKeywordAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tags: tuple[ImapKeywordTag, ...] = Field(default=(), max_length=APPLICATION_LIMITS.flags)

    @model_validator(mode="after")
    def _unique_mappings(self) -> ImapKeywordAccount:
        names = [tag.name for tag in self.tags]
        keywords = [tag.keyword for tag in self.tags]
        if len(names) != len(set(names)):
            raise ValueError("tag names must be unique within an account")
        if len(keywords) != len(set(keywords)):
            raise ValueError("tag keywords must be unique within an account")
        return self


class _ImapKeywordFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accounts: dict[str, ImapKeywordAccount] = Field(
        default_factory=dict,
        max_length=APPLICATION_LIMITS.configured_accounts,
    )

    @field_validator("accounts")
    @classmethod
    def _validate_account_names(cls, value: dict[str, ImapKeywordAccount]) -> dict[str, ImapKeywordAccount]:
        for account_name in value:
            validate_controlled_string(
                account_name,
                field_name="tag account name",
                maximum_bytes=APPLICATION_LIMITS.account_name_bytes,
            )
        return value


@dataclass(frozen=True)
class ImapKeywordRegistry:
    """Immutable process-scoped semantic keyword configuration."""

    accounts: dict[str, ImapKeywordAccount]

    @classmethod
    def load(cls, path: Path | None = None) -> ImapKeywordRegistry:
        source = path or Path(os.path.abspath(Path(DEFAULT_IMAP_KEYWORDS_PATH).expanduser()))
        if not source.exists():
            return cls(accounts={})
        if not source.is_file():
            raise ImapKeywordConfigurationError("imap_keywords.toml must be a regular file")
        try:
            raw: Any = tomllib.loads(source.read_text(encoding="utf-8"))
            parsed = _ImapKeywordFile.model_validate(raw)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
            raise ImapKeywordConfigurationError("imap_keywords.toml is invalid") from exc
        return cls(accounts=dict(parsed.accounts))

    def tags_for(self, account_name: str) -> tuple[ImapKeywordTag, ...]:
        account = self.accounts.get(account_name)
        return account.tags if account is not None else ()

    def resolve(self, account_name: str, values: tuple[str, ...], *, require_writable: bool = False) -> tuple[str, ...]:
        tags = self.tags_for(account_name)
        by_name = {tag.name: tag for tag in tags}
        by_keyword = {tag.keyword: tag for tag in tags}
        resolved: list[str] = []
        for value in values:
            tag = by_name.get(value) or by_keyword.get(value)
            if tag is None:
                raise ValueError(f"Unknown configured email tag: {value}")
            if require_writable and not tag.writable:
                raise PermissionError(f"Email tag is not writable: {value}")
            if tag.keyword not in resolved:
                resolved.append(tag.keyword)
        return tuple(resolved)

    def semantic_names(self, account_name: str, keywords: list[str] | tuple[str, ...]) -> list[str]:
        keyword_set = set(keywords)
        return [tag.name for tag in self.tags_for(account_name) if tag.keyword in keyword_set]

    def writable_keywords(self, account_name: str) -> tuple[str, ...]:
        return tuple(tag.keyword for tag in self.tags_for(account_name) if tag.writable)
