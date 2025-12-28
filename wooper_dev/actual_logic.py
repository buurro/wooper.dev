import io
import json
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Iterable

import psycopg
from packaging.requirements import Requirement
from packaging.version import Version
from psycopg import sql
from psycopg.errors import ConnectionFailure
from pydantic import BaseModel, ConfigDict, computed_field

# Load quickshell lock once at module level
_quickshell_lock_path = Path(__file__).parent / "quickshell.lock.json"
QUICKSHELL_LOCK: dict[str, Any] = json.loads(_quickshell_lock_path.read_text())

# Packages that require explicit naming (ambiguous or deprecated)
AMBIGUOUS_PACKAGES: dict[str, str] = {
    "python": "use `python2` or `python3`",
}


def check_ambiguous(req: Requirement) -> None:
    """Raise if package name is ambiguous."""
    if req.name in AMBIGUOUS_PACKAGES:
        raise ValueError(
            f"Ambiguous package `{req.name}`: {AMBIGUOUS_PACKAGES[req.name]}"
        )


class NixpkgsRev(BaseModel):
    model_config = ConfigDict(frozen=True)

    rev: str
    hash: str
    date: int

    def __gt__(self, other: "NixpkgsRev") -> bool:
        return self.date > other.date

    def __hash__(self) -> int:
        return hash(self.rev)


class Package(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    version: Version
    nixpkgs_rev: NixpkgsRev
    input_name_override: str | None = None

    @computed_field
    @property
    def input_name(self) -> str:
        return self.input_name_override or f"n-{self.name}"

    def __gt__(self, other: "Package") -> bool:
        return self.version > other.version or (
            self.version == other.version and self.nixpkgs_rev > other.nixpkgs_rev
        )


class RevPerDay(BaseModel):
    date: str
    count: int


async def packages_from_string(packages: str) -> list[Package]:
    # Parse requirements and check for ambiguous names
    requirements = [Requirement(p) for p in packages.split(";")]
    for req in requirements:
        check_ambiguous(req)

    candidates = await get_all_candidates(requirements)

    # Check for missing packages
    missing = [req.name for req in requirements if not candidates.get(req.name)]
    if missing:
        raise ValueError(f"Package not found: {'; '.join(missing)}")

    return select_optimal_packages(requirements, candidates)


async def get_all_candidates(
    requirements: list[Requirement],
) -> dict[str, list[Package]]:
    """Get all valid package candidates for each requirement."""
    connection_info = os.getenv("WOOPER_DB")
    if not connection_info:
        raise ConnectionFailure("WOOPER_DB environment variable is not set")

    package_names = [req.name for req in requirements]
    req_by_name = {req.name: req for req in requirements}

    async with await psycopg.AsyncConnection.connect(connection_info) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT DISTINCT package, version FROM packages WHERE package = ANY(%s)",
                (package_names,),
            )
            version_rows = await cursor.fetchall()

            # Find max version matching specifier for each package
            max_versions: dict[str, Version] = {}
            for pkg_name, ver_str in version_rows:
                version = Version(ver_str)
                if version in req_by_name[pkg_name].specifier:
                    if pkg_name not in max_versions or version > max_versions[pkg_name]:
                        max_versions[pkg_name] = version

            if not max_versions:
                return {req.name: [] for req in requirements}

            # Fetch rows with max versions
            conditions = [
                sql.SQL("(package = {} AND version = {})").format(
                    sql.Literal(name), sql.Literal(str(ver))
                )
                for name, ver in max_versions.items()
            ]
            await cursor.execute(
                sql.SQL(
                    "SELECT p.package, p.version, r.rev, r.hash, r.date "
                    "FROM packages p JOIN revs r ON p.rev = r.rev WHERE {}"
                ).format(sql.SQL(" OR ").join(conditions))
            )
            rows = await cursor.fetchall()

    candidates: dict[str, list[Package]] = {req.name: [] for req in requirements}
    for pkg_name, ver_str, rev, hash_, date in rows:
        candidates[pkg_name].append(
            Package(
                name=pkg_name,
                version=Version(ver_str),
                nixpkgs_rev=NixpkgsRev(rev=rev, hash=hash_, date=date),
            )
        )
    return candidates


def select_optimal_packages(
    requirements: list[Requirement],
    candidates: dict[str, list[Package]],
) -> list[Package]:
    """Select packages using minimum nixpkgs revisions while maximizing versions."""
    names = [req.name for req in requirements]
    duplicates = [name for name in names if names.count(name) > 1]
    if duplicates:
        raise ValueError(f"Duplicate package: {duplicates[0]}")

    max_versions: dict[str, Version] = {}
    for req in requirements:
        pkg_candidates = candidates.get(req.name, [])
        if not pkg_candidates:
            raise ValueError(f"Version not found for package {req.name}")
        max_versions[req.name] = max(p.version for p in pkg_candidates)

    # Find revisions that have max version for each package
    optimal_revs: dict[str, set[NixpkgsRev]] = {
        req.name: {
            p.nixpkgs_rev
            for p in candidates[req.name]
            if p.version == max_versions[req.name]
        }
        for req in requirements
    }

    # Greedy set cover: select minimum revisions to cover all packages
    uncovered = set(req.name for req in requirements)
    selected_revs: list[NixpkgsRev] = []

    while uncovered:
        all_revs = {rev for name in uncovered for rev in optimal_revs[name]}
        best_rev = max(
            all_revs,
            key=lambda r: (len({n for n in uncovered if r in optimal_revs[n]}), r.date),
        )
        selected_revs.append(best_rev)
        uncovered -= {n for n in uncovered if best_rev in optimal_revs[n]}

    rev_to_input = {rev.rev: f"n{i}" for i, rev in enumerate(selected_revs)}
    selected_rev_set = set(selected_revs)

    result: list[Package] = []
    for req in requirements:
        matching = [
            p
            for p in candidates[req.name]
            if p.version == max_versions[req.name] and p.nixpkgs_rev in selected_rev_set
        ]
        best = max(matching, key=lambda p: p.nixpkgs_rev.date)
        best.input_name_override = rev_to_input[best.nixpkgs_rev.rev]
        result.append(best)

    return result


async def get_package(requirement: Requirement) -> Package | None:
    """Get the best package for a single requirement."""
    check_ambiguous(requirement)

    connection_info = os.getenv("WOOPER_DB")
    if not connection_info:
        raise ConnectionFailure("WOOPER_DB environment variable is not set")

    async with await psycopg.AsyncConnection.connect(connection_info) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT DISTINCT version FROM packages WHERE package = %s",
                (requirement.name,),
            )
            versions = await cursor.fetchall()

            matching = [
                Version(v[0])
                for v in versions
                if Version(v[0]) in requirement.specifier
            ]
            if not matching:
                return None

            await cursor.execute(
                "SELECT p.version, r.rev, r.hash, r.date "
                "FROM packages p JOIN revs r ON p.rev = r.rev "
                "WHERE package = %s AND version = %s ORDER BY r.date DESC LIMIT 1",
                (requirement.name, str(max(matching))),
            )
            row = await cursor.fetchone()

    if not row:
        return None
    return Package(
        name=requirement.name,
        version=Version(row[0]),
        nixpkgs_rev=NixpkgsRev(rev=row[1], hash=row[2], date=row[3]),
    )


async def get_revs_per_day() -> list[RevPerDay]:
    """Get the count of revisions per day."""
    connection_info = os.getenv("WOOPER_DB")
    if not connection_info:
        raise ConnectionFailure("WOOPER_DB environment variable is not set")

    async with await psycopg.AsyncConnection.connect(connection_info) as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT DATE(TO_TIMESTAMP(date)) as day, COUNT(*) "
                "FROM revs GROUP BY day ORDER BY day DESC"
            )
            rows = await cursor.fetchall()

    return [RevPerDay(date=str(row[0]), count=row[1]) for row in rows]


def get_flake_nix(packages: Iterable[Package], spec: str = "") -> str:
    packages = list(packages)

    # Collect unique inputs
    inputs: dict[str, NixpkgsRev] = {}
    for pkg in packages:
        if pkg.input_name not in inputs:
            inputs[pkg.input_name] = pkg.nixpkgs_rev

    first = next(iter(inputs))

    # Build components
    input_urls = "\n    ".join(
        f'{name}.url = "github:nixos/nixpkgs?rev={rev.rev}";'
        for name, rev in inputs.items()
    )
    input_names = ", ".join(inputs)
    pkg_list = ("\n" + " " * 10).join(
        f"{pkg.input_name}.legacyPackages.${{s}}.{pkg.name}" for pkg in packages
    )
    pkg_attrs = ("\n" + " " * 6).join(
        f"{pkg.name} = {pkg.input_name}.legacyPackages.${{system}}.{pkg.name};"
        for pkg in packages
    )

    comment = f"Regenerate: nix build 'https://wooper.dev/{spec}' && cat result/bin/wooper" if spec else ""

    return f"""\
{{
  inputs = {{
    quickshell.url = "github:buurro/quickshell";
    {input_urls}
  }};

  outputs = {{ quickshell, {input_names}, ... }}: let
    shells = quickshell.lib.mkPackages {first} {{
      wooper = {{
        packages = pkgs: let s = pkgs.stdenv.hostPlatform.system; in [
          {pkg_list}
        ];
        comment = "{comment}";
      }};
    }};
  in {{
    packages = builtins.mapAttrs (system: shellPkgs: shellPkgs // {{
      default = shellPkgs.wooper;
      {pkg_attrs}
    }}) shells;
  }};
}}
"""


def get_flake_lock(packages: Iterable[Package]) -> str:
    packages = list(packages)
    seen_inputs: dict[str, Package] = {}
    for pkg in packages:
        if pkg.input_name not in seen_inputs:
            seen_inputs[pkg.input_name] = pkg

    lock: dict[str, Any] = {
        "nodes": {
            "quickshell": {
                "inputs": {},
                "locked": QUICKSHELL_LOCK,
                "original": {"owner": "buurro", "repo": "quickshell", "type": "github"},
            },
            "root": {"inputs": {"quickshell": "quickshell"}},
        },
        "root": "root",
        "version": 7,
    }

    for name, pkg in seen_inputs.items():
        lock["nodes"][name] = {
            "locked": {
                "lastModified": pkg.nixpkgs_rev.date,
                "narHash": pkg.nixpkgs_rev.hash,
                "owner": "nixos",
                "repo": "nixpkgs",
                "rev": pkg.nixpkgs_rev.rev,
                "type": "github",
            },
            "original": {
                "owner": "nixos",
                "repo": "nixpkgs",
                "rev": pkg.nixpkgs_rev.rev,
                "type": "github",
            },
        }
        lock["nodes"]["root"]["inputs"][name] = name

    return json.dumps(lock)


def get_flake_tarball(packages: Iterable[Package], spec: str = "") -> io.BytesIO:
    packages = list(packages)
    with tempfile.TemporaryDirectory() as dir_path:
        (Path(dir_path) / "flake.nix").write_text(get_flake_nix(packages, spec))
        (Path(dir_path) / "flake.lock").write_text(get_flake_lock(packages))

        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
            tar.add(dir_path, arcname=".")

    tar_bytes.seek(0)
    return tar_bytes
