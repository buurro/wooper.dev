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
from packaging.requirements import Requirement
from packaging.version import Version
from psycopg import sql
from psycopg.errors import ConnectionFailure

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
        raise ValueError(f"Ambiguous package `{req.name}`: {AMBIGUOUS_PACKAGES[req.name]}")


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
        return self._input_name or f"n-{self.name}"

    def __gt__(self, other: "Package") -> bool:
        return self.version > other.version or (
            self.version == other.version and self.nixpkgs_rev > other.nixpkgs_rev
        )


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
        req.name: {p.nixpkgs_rev for p in candidates[req.name] if p.version == max_versions[req.name]}
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

    rev_to_input = {rev.rev: f"nixpkgs-{i}" for i, rev in enumerate(selected_revs)}
    selected_rev_set = set(selected_revs)

    result: list[Package] = []
    for req in requirements:
        matching = [
            p for p in candidates[req.name]
            if p.version == max_versions[req.name] and p.nixpkgs_rev in selected_rev_set
        ]
        best = max(matching, key=lambda p: p.nixpkgs_rev.date)
        best._input_name = rev_to_input[best.nixpkgs_rev.rev]
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

            matching = [Version(v[0]) for v in versions if Version(v[0]) in requirement.specifier]
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


async def get_revs_per_day() -> list[dict[str, Any]]:
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

    return [{"date": str(row[0]), "count": row[1]} for row in rows]


def get_flake_nix(packages: Iterable[Package]) -> str:
    packages = list(packages)
    seen_inputs: dict[str, Package] = {}
    for pkg in packages:
        if pkg.input_name not in seen_inputs:
            seen_inputs[pkg.input_name] = pkg

    first_input = next(iter(seen_inputs.keys()))

    inputs = "\n".join(
        f'    "{name}".url = "github:nixos/nixpkgs?rev={pkg.nixpkgs_rev.rev}";'
        for name, pkg in seen_inputs.items()
    )

    packages_by_input: dict[str, list[str]] = {}
    for pkg in packages:
        packages_by_input.setdefault(pkg.input_name, []).append(pkg.name)

    packages_for = "\n".join(
        f'            (with inputs."{name}".legacyPackages.${{pkgs.stdenv.hostPlatform.system}}; [{" ".join(names)}])'
        for name, names in packages_by_input.items()
    )

    individual_pkgs = "\n".join(
        f'{pkg.name} = inputs."{pkg.input_name}".legacyPackages.${{pkgs.stdenv.hostPlatform.system}}.{pkg.name};'
        for pkg in packages
    ).replace("\n", "\n                ")

    return dedent(f"""\
        {{
          inputs = {{
            quickshell.url = "github:buurro/quickshell";
        {inputs}
          }};
          outputs = {{
            self,
            quickshell,
            ...
          }} @ inputs: let
            inherit (quickshell.lib) mkDevshell toPackages forAllSystems;
            shells = toPackages {{
              dev = mkDevshell {{
                nixpkgs = inputs."{first_input}";
                packagesFor = pkgs:
                  builtins.concatLists [
        {packages_for}
                  ];
              }};
            }};
          in {{
            packages = forAllSystems inputs."{first_input}" (pkgs:
              shells.${{pkgs.stdenv.hostPlatform.system}}
              // {{
                default = shells.${{pkgs.stdenv.hostPlatform.system}}.dev;
                {individual_pkgs}
              }});
          }};
        }}
        """)


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


def get_flake_tarball(packages: Iterable[Package]) -> io.BytesIO:
    packages = list(packages)
    with tempfile.TemporaryDirectory() as dir_path:
        (Path(dir_path) / "flake.nix").write_text(get_flake_nix(packages))
        (Path(dir_path) / "flake.lock").write_text(get_flake_lock(packages))

        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
            tar.add(dir_path, arcname=".")

    tar_bytes.seek(0)
    return tar_bytes
