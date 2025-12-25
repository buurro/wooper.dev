import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.errors import ConnectionFailure

from .hydra import get_builds


def main() -> None:
    parser = argparse.ArgumentParser(description="update stuff")
    parser.add_argument(
        "--before", type=str, help="check builds before this YYYY-MM-DD"
    )
    parser.add_argument("--after", type=str, help="check builds after this YYYY-MM-DD")
    args = parser.parse_args()

    before = (
        datetime.strptime(args.before, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.before
        else None
    )
    after = (
        datetime.strptime(args.after, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.after
        else None
    )

    connection_info = os.getenv("WOOPER_DB")
    if not connection_info:
        raise ConnectionFailure("WOOPER_DB environment variable is not set")

    conn = psycopg.connect(connection_info)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS revs (
            rev TEXT PRIMARY KEY,
            hash TEXT,
            date INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS packages (
            rev TEXT,
            package TEXT,
            version TEXT,
            UNIQUE (rev, package),
            FOREIGN KEY (rev) REFERENCES revs(rev)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_packages_package
        ON packages (package)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_packages_package_version
        ON packages (package, version)
    """)

    for i, info in enumerate(get_builds(before, after)):
        print(f"{i}: {info.date} {info.ref}")

        ref = info.ref

        command = [
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "flake",
            "metadata",
            f"github:nixos/nixpkgs?ref={ref}",  # ref here is the short commit hash
            "--json",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)

        metadata = json.loads(result.stdout)

        rev = metadata["locked"]["rev"]
        nar_hash = metadata["locked"]["narHash"]
        date = metadata["locked"]["lastModified"]
        store_path = metadata["path"]

        cursor.execute(
            """
            INSERT INTO revs (rev, hash, date)
            VALUES (%s, %s, %s)
            ON CONFLICT (rev) DO NOTHING
            """,
            (rev, nar_hash, date),
        )

        command = [
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "search",
            f"github:nixos/nixpkgs?rev={rev}",
            "^",
            "--json",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)

        # Parse the JSON output
        data: dict[str, Any] = json.loads(result.stdout)

        for package_name, details in data.items():
            package_name = package_name.split(".", 2)[2]
            version = details.get("version", "")

            cursor.execute(
                """
                INSERT INTO packages (rev, package, version)
                VALUES (%s, %s, %s)
                ON CONFLICT (rev, package) DO NOTHING
                """,
                (rev, package_name, version),
            )
        conn.commit()

        # Clean up the fetched nixpkgs store path
        subprocess.run(
            [
                "nix",
                "--extra-experimental-features",
                "nix-command",
                "store",
                "delete",
                store_path,
            ],
            capture_output=True,
            check=False,
        )

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
