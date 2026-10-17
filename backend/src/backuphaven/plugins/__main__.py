"""Check a plugin and its fixture: ``python -m backuphaven.plugins check …``.

    uv run python -m backuphaven.plugins check plugins/bundled/myapp.yaml \\
        --fixture tests/plugin_fixtures/myapp.yaml

Validates the manifest (plugin API v1) and, with ``--fixture``, backs the sample
up, wipes it and restores it (ADR-0013). Exit status 0 when everything passes.
"""

import argparse
import sys
from pathlib import Path

from backuphaven.plugins import fixture
from backuphaven.plugins.manifest import ManifestError, parse_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backuphaven.plugins")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="validate a plugin and run its fixture")
    check.add_argument("manifest", type=Path)
    check.add_argument("--fixture", type=Path)
    args = parser.parse_args(argv)

    try:
        manifest = parse_manifest(args.manifest.read_text())
    except (OSError, ManifestError) as exc:
        print(f"✗ {args.manifest}: {exc}")
        return 1
    print(f"✓ {args.manifest}: plugin {manifest.id} {manifest.version} is valid")
    if args.fixture is None:
        return 0
    try:
        sample = fixture.load(args.fixture)
    except (OSError, ValueError) as exc:
        print(f"✗ {args.fixture}: {exc}")
        return 1
    try:
        problems = fixture.round_trip(manifest, sample)
    except fixture.DatabaseServerMissingError as exc:
        print(f"✗ {exc}")
        return 1
    for problem in problems:
        print(f"✗ {problem}")
    if not problems:
        print(f"✓ {args.fixture}: backed up, wiped and restored as expected")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
