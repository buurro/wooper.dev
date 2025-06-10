from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse, StreamingResponse
from packaging.requirements import Requirement

from .actual_logic import (
    NixpkgsRev,
    get_flake_nix,
    get_package,
    get_tarball,
    packages_from_string,
)

app = FastAPI()


@app.get("/flake/{packages}")
async def flake(packages: str):
    try:
        packages_list = await packages_from_string(packages)
    except ValueError:
        raise HTTPException(
            status_code=404, detail="Package not found at specified version"
        )

    flake_nix = await get_flake_nix(packages_list)

    return PlainTextResponse(flake_nix)


@app.get("/{packages}")
async def tarball(packages: str) -> StreamingResponse:
    try:
        packages_list = await packages_from_string(packages)
    except ValueError:
        raise HTTPException(
            status_code=404, detail="Package not found at specified version"
        )

    tar_stream = await get_tarball(packages_list)

    filename = "flake.tar.gz"
    return StreamingResponse(
        tar_stream,
        media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/nixpkgs/{requirement}")
async def nixpkgs(requirement: str) -> RedirectResponse:
    package = await get_package(Requirement(requirement))
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
    package = await get_package(Requirement(requirement))
    if not package:
        raise HTTPException(
            status_code=404, detail="Package not found at specified version"
        )

    return package.nixpkgs_rev
