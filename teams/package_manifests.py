#!/usr/bin/env python3
"""Render Teams templates and package their JSON files and icons."""

import json
from pathlib import Path
import re
import sys
from zipfile import ZIP_DEFLATED, ZipFile


PLACEHOLDER = re.compile(r"<([A-Z][A-Z0-9_]*)>")


def read_env(path):
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"{path.name}:{number}: expected KEY=value")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"{path.name}:{number}: unmatched quote")
            value = value[1:-1]
        values[key] = value
    return values


def render(data, values):
    def replace(match):
        key = match[1]
        if not values.get(key) or PLACEHOLDER.search(values[key]):
            raise ValueError(f"Set {key} in teams.env to a nonempty value")
        return values[key]

    if isinstance(data, str):
        return PLACEHOLDER.sub(replace, data)
    if isinstance(data, list):
        return [render(item, values) for item in data]
    if isinstance(data, dict):
        return {render(key, values): render(value, values) for key, value in data.items()}
    return data


def package_manifests(teams_dir):
    values = read_env(teams_dir / "teams.env")
    manifests = sorted(teams_dir.glob("*/*manifest.json"))
    if not manifests:
        raise ValueError("No Teams manifests found")

    packages = []
    for manifest in manifests:
        entries = {}
        for source in sorted(manifest.parent.rglob("*")):
            if not source.is_file() or source.suffix not in {".json", ".png"}:
                continue
            name = "manifest.json" if source == manifest else source.relative_to(manifest.parent).as_posix()
            if name in entries:
                raise ValueError(f"Duplicate package entry: {name}")
            if source.suffix == ".json":
                data = json.loads(source.read_text(encoding="utf-8"))
                entries[name] = (json.dumps(render(data, values), indent=2) + "\n").encode("utf-8")
            else:
                entries[name] = source.read_bytes()
        packages.append((teams_dir / "dist" / f"{manifest.parent.name}.zip", entries))

    # Validate every template before creating or replacing any archives.
    (teams_dir / "dist").mkdir(exist_ok=True)
    for destination, entries in packages:
        with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
    return [destination for destination, _ in packages]


def main():
    try:
        packages = package_manifests(Path(__file__).resolve().parent)
    except (OSError, ValueError) as error:
        print(f"Packaging failed: {error}", file=sys.stderr)
        return 1
    for package in packages:
        print(f"Created {package}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
