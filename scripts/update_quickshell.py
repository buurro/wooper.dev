#!/usr/bin/env python3
"""Update the quickshell.lock.json file with latest metadata from GitHub."""

import json
import subprocess
from pathlib import Path


def main() -> None:
    print("Fetching quickshell metadata...")
    result = subprocess.run(
        [
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "flake",
            "metadata",
            "github:buurro/quickshell",
            "--json",
            "--refresh",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    metadata = json.loads(result.stdout)
    locked = metadata["locked"]
    locked.pop("__final", None)  # nix internal field, breaks flake.lock

    print(f"  rev: {locked['rev']}")
    print(f"  narHash: {locked['narHash']}")
    print(f"  lastModified: {locked['lastModified']}")

    lock_path = Path(__file__).parent.parent / "wooper_dev" / "quickshell.lock.json"
    with open(lock_path, "w") as f:
        json.dump(locked, f, indent=2)
        f.write("\n")

    print(f"Updated {lock_path}")


if __name__ == "__main__":
    main()
