"""Capture the data dictionary from the live Oracle (T1.4).

    .\\.venv\\Scripts\\python.exe scripts\\capture_dictionary.py

Writes corpus/dictionary.json. Committed on purpose: it lets name-resolution tests run
without a database, and it is the schema snapshot that must be versioned alongside the
code snapshot. Re-capture after changing the corpus DDL.

The printed fingerprint is what an analysis run pins itself to. If the dictionary moves
and a run still claims that fingerprint, resolution fails loudly instead of quietly
binding to different objects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lineage.oracle import OracleSettings, connect
from lineage.resolution.dictionary import Dictionary, capture

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "corpus" / "dictionary.json"


def main() -> None:
    settings = OracleSettings.from_env()
    print(f"capturing dictionary from {settings.dsn} as {settings.user}")

    with connect(settings) as connection:
        dictionary = capture(
            connection,
            schema=settings.user,
            captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    dictionary.save(OUTPUT)

    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    print(f"  objects     {len(dictionary.objects)}")
    print(f"  synonyms    {len(dictionary.synonyms)}")
    print(f"  relations   {len(dictionary.columns)} with columns")
    print(f"  views       {len(dictionary.view_text)}")
    print(f"  fingerprint {dictionary.fingerprint()}")

    if dictionary.synonyms:
        print("\nsynonym redirects captured (the silent-failure defence):")
        for name, target in sorted(dictionary.synonyms.items()):
            print(f"  {name} -> {target}")

    # Sanity check: a saved dictionary must reload to the same fingerprint, or pinning
    # would fail on every run and be ignored as noise.
    reloaded = Dictionary.load(OUTPUT)
    if reloaded.fingerprint() != dictionary.fingerprint():
        raise SystemExit("fingerprint changed on round trip - serialisation is not stable")


if __name__ == "__main__":
    main()
