# Saves and loads user-added price sources.

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from feedverdict.custom_sources import (
    SourceConfigError,
    SourceDefinition,
    build_custom_source,
    load_source_definition,
)
from feedverdict.http import JsonHttpClient
from feedverdict.models import SourceFailure
from feedverdict.paths import app_home
from feedverdict.sources import PriceSource


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    path: Path
    definition: SourceDefinition | None
    error: str | None = None


class SourceRegistry:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or (app_home() / "sources")

    @staticmethod
    def _slug(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        if not slug:
            raise SourceConfigError("Source name must contain a letter or number")
        return slug

    def add(self, config_path: Path, *, replace: bool = False) -> Path:
        definition = load_source_definition(config_path.resolve())
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.directory / f"{self._slug(definition.name)}.json"
        if target.exists() and not replace:
            raise SourceConfigError(
                f"A source named {definition.name!r} is already registered; use --replace"
            )

        encoded = (
            json.dumps(definition.to_payload(), indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            dir=self.directory,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, target)
        finally:
            temporary_path.unlink(missing_ok=True)
        return target

    def entries(self) -> tuple[RegistryEntry, ...]:
        if not self.directory.exists():
            return ()
        entries: list[RegistryEntry] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                entries.append(RegistryEntry(path, load_source_definition(path)))
            except SourceConfigError as exc:
                entries.append(RegistryEntry(path, None, str(exc)))
        return tuple(entries)

    def load_sources(
        self,
        client: JsonHttpClient,
    ) -> tuple[tuple[PriceSource, ...], tuple[SourceFailure, ...]]:
        sources: list[PriceSource] = []
        failures: list[SourceFailure] = []
        seen_names: set[str] = set()
        for entry in self.entries():
            if entry.definition is None:
                failures.append(
                    SourceFailure(
                        entry.path.stem,
                        "CONFIG_INVALID",
                        entry.error or "Invalid config",
                    )
                )
                continue
            name_key = entry.definition.name.casefold()
            if name_key in seen_names:
                failures.append(
                    SourceFailure(
                        entry.definition.name,
                        "CONFIG_DUPLICATE_NAME",
                        "Another registered source already uses this name",
                    )
                )
                continue
            seen_names.add(name_key)
            sources.append(build_custom_source(entry.definition, client))
        return tuple(sources), tuple(failures)
