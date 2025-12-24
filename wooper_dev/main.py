from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse, StreamingResponse
from packaging.requirements import InvalidRequirement, Requirement
from psycopg.errors import ConnectionFailure

from .actual_logic import (
    NixpkgsRev,
    get_flake_nix,
    get_package,
    get_tarball,
    packages_from_string,
)

app = FastAPI()

MAX_PACKAGES = 50


def _parse_requirement(req_str: str) -> Requirement:
    """Parse a requirement string with error handling."""
    try:
        return Requirement(req_str)
    except InvalidRequirement as e:
        raise HTTPException(status_code=400, detail=f"Invalid requirement: {e}")


@app.get("/flake/{packages}")
async def flake(packages: str):
    parts = packages.split(";")
    if len(parts) > MAX_PACKAGES:
        raise HTTPException(
            status_code=400, detail=f"Too many packages (max {MAX_PACKAGES})"
        )

    try:
        packages_list = await packages_from_string(packages)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionFailure:
        raise HTTPException(status_code=503, detail="Database unavailable")

    flake_nix = await get_flake_nix(packages_list)

    return PlainTextResponse(flake_nix)


@app.get("/{packages}")
async def tarball(packages: str) -> StreamingResponse:
    parts = packages.split(";")
    if len(parts) > MAX_PACKAGES:
        raise HTTPException(
            status_code=400, detail=f"Too many packages (max {MAX_PACKAGES})"
        )

    try:
        packages_list = await packages_from_string(packages)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionFailure:
        raise HTTPException(status_code=503, detail="Database unavailable")

    tar_stream = await get_tarball(packages_list)

    filename = "flake.tar.gz"
    return StreamingResponse(
        tar_stream,
        media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/nixpkgs/{requirement}")
async def nixpkgs(requirement: str) -> RedirectResponse:
    req = _parse_requirement(requirement)
    try:
        package = await get_package(req)
    except ConnectionFailure:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not package:
        raise HTTPException(
            status_code=404, detail="Package not found at specified version"
        )

    return RedirectResponse(
        url=f"https://github.com/NixOS/nixpkgs/archive/{package.nixpkgs_rev.rev}.tar.gz",
        status_code=301,
    )


@app.get("/rev/{requirement}")
async def rev(requirement: str) -> NixpkgsRev:
    req = _parse_requirement(requirement)
    try:
        package = await get_package(req)
    except ConnectionFailure:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not package:
        raise HTTPException(
            status_code=404, detail="Package not found at specified version"
        )

    return package.nixpkgs_rev
