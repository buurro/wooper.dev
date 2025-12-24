import io
import json
import os
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Any, Iterable

import psycopg
from psycopg import sql
from packaging.requirements import Requirement
from packaging.version import Version
from psycopg.errors import ConnectionFailure


@dataclass(frozen=True)
class NixpkgsRev:
    rev: str
    hash: str
    date: int

    def __gt__(self, other: "NixpkgsRev") -> bool:
        return self.date > other.date

    def __hash__(self) -> int:
        return hash(self.rev)


@dataclass
class Package:
    name: str
    version: Version
    nixpkgs_rev: NixpkgsRev
    _input_name: str | None = None

    @property
    def input_name(self) -> str:
        if self._input_name:
            return self._input_name
        return f"n-{self.name}"

    def __gt__(self, other: "Package") -> bool:
        return self.version > other.version or (
            self.version == other.version and self.nixpkgs_rev > other.nixpkgs_rev
        )


async def packages_from_string(packages: str) -> list[Package]:
    requirements = [Requirement(p) for p in packages.split(";")]
    candidates = await get_all_candidates(requirements)
    return select_optimal_packages(requirements, candidates)


async def get_all_candidates(
    requirements: list[Requirement],
) -> dict[str, list[Package]]:
    """Get all valid package candidates for each requirement.

    Fetches distinct versions per package, finds max matching specifier in Python,
    then fetches only rows with that max version.
    """
    connection_info = os.getenv("WOOPER_DB")
    if not connection_info:
        raise ConnectionFailure("WOOPER_DB environment variable is not set")

    package_names = [req.name for req in requirements]
    req_by_name = {req.name: req for req in requirements}

    async with await psycopg.AsyncConnection.connect(connection_info) as conn:
        async with conn.cursor() as cursor:
            # Step 1: Get distinct versions per package (uses index, returns few rows)
            await cursor.execute(
                """
                select distinct package, version
                from packages
                where package = any(%s)
                """,
                (package_names,),
            )
            version_rows = await cursor.fetchall()

            # Find max version matching specifier for each package (in Python)
            max_versions: dict[str, Version] = {}
            for pkg_name, ver_str in version_rows:
                version = Version(ver_str)
                req = req_by_name[pkg_name]
                if version in req.specifier:
                    if pkg_name not in max_versions or version > max_versions[pkg_name]:
                        max_versions[pkg_name] = version

            # Step 2: Fetch only rows with max version
            if not max_versions:
                return {req.name: [] for req in requirements}

            conditions = [
                sql.SQL("(package = {} AND version = {})").format(
                    sql.Literal(pkg_name), sql.Literal(str(max_ver))
                )
                for pkg_name, max_ver in max_versions.items()
            ]

            query = sql.SQL(
                """
                select packages.package, packages.version, revs.rev, revs.hash, revs.date
                from packages
                    join revs on packages.rev = revs.rev
                where {}
                """
            ).format(sql.SQL(" OR ").join(conditions))

            await cursor.execute(query)
            rows = await cursor.fetchall()

    # Build candidates dict
    candidates: dict[str, list[Package]] = {req.name: [] for req in requirements}
    for row in rows:
        pkg_name, ver_str, rev, hash_, date = row
        version = Version(ver_str)
        nixpkgs_rev = NixpkgsRev(rev=rev, hash=hash_, date=date)
        candidates[pkg_name].append(
            Package(name=pkg_name, version=version, nixpkgs_rev=nixpkgs_rev)
        )

    return candidates


def select_optimal_packages(
    requirements: list[Requirement],
    candidates: dict[str, list[Package]],
) -> list[Package]:
    """Select packages using minimum nixpkgs revisions while maximizing versions."""
    # For each package, find the maximum version available
    max_versions: dict[str, Version] = {}
    for req in requirements:
        pkg_candidates = candidates.get(req.name, [])
        if not pkg_candidates:
            raise ValueError(f"Version not found for package {req.name}")
        max_versions[req.name] = max(p.version for p in pkg_candidates)

    # For each package, find all revisions that have the max version
    # Map: package_name -> set of revisions with max version
    optimal_revs: dict[str, set[NixpkgsRev]] = {}
    for req in requirements:
        max_ver = max_versions[req.name]
        optimal_revs[req.name] = {
            p.nixpkgs_rev for p in candidates[req.name] if p.version == max_ver
        }

    # Greedy set cover: select minimum revisions to cover all packages
    uncovered = set(req.name for req in requirements)
    selected_revs: list[NixpkgsRev] = []

    while uncovered:
        # Find the revision that covers the most uncovered packages
        # Prefer newer revisions as tiebreaker
        best_rev: NixpkgsRev | None = None
        best_coverage: set[str] = set()

        all_revs: set[NixpkgsRev] = set()
        for name in uncovered:
            all_revs.update(optimal_revs[name])

        for rev in all_revs:
            coverage = {name for name in uncovered if rev in optimal_revs[name]}
            if len(coverage) > len(best_coverage) or (
                len(coverage) == len(best_coverage)
                and best_rev is not None
                and rev > best_rev
            ):
                best_rev = rev
                best_coverage = coverage

        if best_rev is None:
            break

        selected_revs.append(best_rev)
        uncovered -= best_coverage

    # Create rev -> input_name mapping
    rev_to_input: dict[str, str] = {}
    for i, rev in enumerate(selected_revs):
        rev_to_input[rev.rev] = f"nixpkgs-{i}"

    # For each package, pick the selected revision (prefer newer if multiple match)
    result: list[Package] = []
    selected_rev_set = set(selected_revs)
    for req in requirements:
        max_ver = max_versions[req.name]
        matching = [
            p
            for p in candidates[req.name]
            if p.version == max_ver and p.nixpkgs_rev in selected_rev_set
        ]
        best = max(matching, key=lambda p: p.nixpkgs_rev.date)
        best._input_name = rev_to_input[best.nixpkgs_rev.rev]
        result.append(best)

    return result


async def get_package(requirement: Requirement) -> Package | None:
    """Get the best package for a single requirement.

    Note: For multiple packages, use get_all_candidates + select_optimal_packages
    which is more efficient and consolidates nixpkgs revisions.
    """
    connection_info = os.getenv("WOOPER_DB")
    if not connection_info:
        raise ConnectionFailure("WOOPER_DB environment variable is not set")

    async with await psycopg.AsyncConnection.connect(connection_info) as conn:
        async with conn.cursor() as cursor:
            # Get distinct versions (uses index, few rows per package)
            await cursor.execute(
                """
                select distinct version
                from packages
                where package = %s
                """,
                (requirement.name,),
            )
            versions = await cursor.fetchall()

            # Find max version matching specifier in Python
            matching = [
                Version(v[0])
                for v in versions
                if Version(v[0]) in requirement.specifier
            ]
            if not matching:
                return None
            target_version = str(max(matching))

            # Fetch only rows with target version, get the newest rev
            await cursor.execute(
                """
                select packages.version, revs.rev, revs.hash, revs.date
                from packages
                    join revs on packages.rev = revs.rev
                where package = %s AND version = %s
                order by revs.date desc
                limit 1
                """,
                (requirement.name, target_version),
            )
            row = await cursor.fetchone()

    if not row:
        return None

    version = Version(row[0])
    rev = NixpkgsRev(rev=row[1], hash=row[2], date=row[3])
    return Package(name=requirement.name, version=version, nixpkgs_rev=rev)


async def get_flake_nix(packages: Iterable[Package]) -> str:
    packages = list(packages)

    # Deduplicate inputs - multiple packages may share the same nixpkgs revision
    seen_inputs: dict[str, Package] = {}
    for package in packages:
        if package.input_name not in seen_inputs:
            seen_inputs[package.input_name] = package

    inputs = "\n".join(
        [
            f'        "{input_name}".url = "github:nixos/nixpkgs?rev={pkg.nixpkgs_rev.rev}";'
            for input_name, pkg in seen_inputs.items()
        ]
    )

    outputs = "\n".join(
        [
            f'                {package.name} = inputs."{package.input_name}".legacyPackages.${{system}}.{package.name};'
            for package in packages
        ]
    )

    list_elements = "\n".join(
        [
            f'                        inputs."{package.input_name}".legacyPackages.${{system}}.{package.name}'
            for package in packages
        ]
    )

    template = dedent("""
        {
            inputs = {
                nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
        %(inputs)s
            };
            outputs = { self, nixpkgs, ... } @ inputs:
                let
                    supportedSystems = [ "x86_64-linux" "x86_64-darwin" "aarch64-linux" "aarch64-darwin" ];
                    forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
                in
                {
                    legacyPackages = forAllSystems (system: inputs.nixpkgs.legacyPackages.${system} // {
        %(outputs)s
                    });
                    packages = forAllSystems (system: {
                        default = nixpkgs.legacyPackages.${system}.buildEnv {
                            name = "wooperShell";
                            paths = [
        %(list_elements)s
                            ];
                        };
                    });
            };
        }
    """)

    flake_nix = template % {
        "inputs": inputs,
        "outputs": outputs,
        "list_elements": list_elements,
    }

    return flake_nix


async def get_flake_lock(packages: Iterable[Package]) -> str:
    packages = list(packages)

    lock: dict[str, Any] = {
        "nodes": {
            "nixpkgs": {
                "locked": {
                    "lastModified": 1747179050,
                    "narHash": "sha256-qhFMmDkeJX9KJwr5H32f1r7Prs7XbQWtO0h3V0a0rFY=",
                    "owner": "nixos",
                    "repo": "nixpkgs",
                    "rev": "adaa24fbf46737f3f1b5497bf64bae750f82942e",
                    "type": "github",
                },
                "original": {
                    "owner": "nixos",
                    "ref": "nixos-unstable",
                    "repo": "nixpkgs",
                    "type": "github",
                },
            },
            "root": {"inputs": {"nixpkgs": "nixpkgs"}},
        },
        "root": "root",
        "version": 7,
    }

    # Deduplicate inputs - multiple packages may share the same nixpkgs revision
    seen_inputs: set[str] = set()
    for package in packages:
        if package.input_name in seen_inputs:
            continue
        seen_inputs.add(package.input_name)

        lock["nodes"][package.input_name] = {
            "locked": {
                "lastModified": package.nixpkgs_rev.date,
                "narHash": package.nixpkgs_rev.hash,
                "owner": "nixos",
                "repo": "nixpkgs",
                "rev": package.nixpkgs_rev.rev,
                "type": "github",
            },
            "original": {
                "owner": "nixos",
                "repo": "nixpkgs",
                "rev": package.nixpkgs_rev.rev,
                "type": "github",
            },
        }
        lock["nodes"]["root"]["inputs"][package.input_name] = package.input_name

    return json.dumps(lock)


async def get_tarball(packages: Iterable[Package]) -> io.BytesIO:
    flake_nix = await get_flake_nix(packages)
    flake_lock = await get_flake_lock(packages)

    with tempfile.TemporaryDirectory() as dir_path:
        with open(Path(dir_path) / "flake.nix", "w") as f:
            f.write(flake_nix)

        with open(Path(dir_path) / "flake.lock", "w") as f:
            f.write(flake_lock)

        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
            tar.add(dir_path, arcname=".")

    tar_bytes.seek(0)
    return tar_bytes
