import io
import json
import os
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Iterable

import psycopg
from packaging.requirements import Requirement
from packaging.version import Version
from psycopg.errors import ConnectionFailure


@dataclass
class NixpkgsRev:
    rev: str
    hash: str
    date: int

    def __gt__(self, other: "NixpkgsRev") -> bool:
        return self.date > other.date


@dataclass
class Package:
    name: str
    version: Version
    nixpkgs_rev: NixpkgsRev

    @property
    def input_name(self) -> str:
        return f"n-{self.name}"

    def __gt__(self, other: "Package") -> bool:
        return self.version > other.version or (
            self.version == other.version and self.nixpkgs_rev > other.nixpkgs_rev
        )


async def packages_from_string(packages: str) -> list[Package]:
    packages_list = []
    for package in packages.split(";"):
        requirement = Requirement(package)
        package = await get_package(requirement)
        if not package:
            raise ValueError(f"Version not found for package {requirement.name}")
        packages_list.append(package)
    return packages_list


async def get_package(requirement: Requirement) -> Package | None:
    connection_info = os.getenv("VERNICE_DB")
    if not connection_info:
        raise ConnectionFailure("VERNICE_DB environment variable is not set")
    conn = psycopg.connect(connection_info)
    cursor = conn.cursor()

    cursor.execute(
        """
        select packages.version, revs.rev, revs.hash, revs.date
        from packages
            join revs on packages.rev = revs.rev
        where package = %s
        """,
        (requirement.name,),
    )

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    out_package: Package | None = None
    for row in rows:
        version = Version(row[0])
        if version not in requirement.specifier:
            continue

        rev = NixpkgsRev(rev=row[1], hash=row[2], date=row[3])
        package = Package(name=requirement.name, version=version, nixpkgs_rev=rev)

        if out_package is None or package > out_package:
            out_package = package

    return out_package


async def get_flake_nix(packages: Iterable[Package]) -> str:
    inputs = "\n".join(
        [
            f'        "{package.input_name}".url = "github:nixos/nixpkgs?rev={package.nixpkgs_rev.rev}"; # {package.version}'
            for package in packages
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
    lock = {
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

    for package in packages:
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

    dir = tempfile.TemporaryDirectory()

    with open(Path(dir.name) / "flake.nix", "w") as f:
        f.write(flake_nix)

    with open(Path(dir.name) / "flake.lock", "w") as f:
        f.write(flake_lock)

    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
        tar.add(dir.name, arcname=".")

    tar_bytes.seek(0)
    return tar_bytes
