from importlib.metadata import PackageNotFoundError, version
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Path
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse

TEMPLATES_DIR = FilePath(__file__).parent / "templates"
from packaging.requirements import InvalidRequirement, Requirement
from psycopg.errors import ConnectionFailure
from pydantic import BaseModel, Field

from .actual_logic import (
    RevPerDay,
    get_flake_nix,
    get_flake_tarball,
    get_package,
    get_revs_per_day,
    packages_from_string,
)

try:
    __version__ = version("wooper-dev")
except PackageNotFoundError:
    __version__ = "dev"

app = FastAPI(
    title="Wooper",
    description="Generate Nix flakes with specific package versions from nixpkgs history.",
    version=__version__,
)

nix_router = APIRouter(tags=["Nix"])
api_router = APIRouter(prefix="/api", tags=["API"])


class NixpkgsRevResponse(BaseModel):
    """A specific nixpkgs revision."""

    rev: str = Field(description="Git commit hash", examples=["abc123def456"])
    hash: str = Field(
        description="Nix store hash (SRI format)", examples=["sha256-xxx"]
    )
    date: int = Field(description="Unix timestamp of the commit", examples=[1700000000])


PackagesPath = Annotated[
    str,
    Path(
        description="Semicolon-separated list of packages with optional version specifiers",
        examples=["python3;nodejs", "uv~=0.5.0;ruff>=0.8.0"],
    ),
]

PackagePath = Annotated[
    str,
    Path(
        description="Package name with optional PEP 440 version specifier",
        examples=["python3", "uv~=0.5.0", "ruff>=0.8.0"],
    ),
]

MAX_PACKAGES = 50


def _parse_requirement(req_str: str) -> Requirement:
    """Parse a requirement string with error handling."""
    try:
        return Requirement(req_str)
    except InvalidRequirement as e:
        raise HTTPException(status_code=400, detail=f"Invalid requirement: {e}")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Landing page for Wooper."""
    html = (TEMPLATES_DIR / "index.html").read_text()
    return HTMLResponse(content=html)


@nix_router.get(
    "/flake/{packages}",
    summary="Get flake.nix content",
    description="Returns the flake.nix file content for the requested packages.",
    response_class=PlainTextResponse,
)
async def flake(packages: PackagesPath) -> PlainTextResponse:
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

    return PlainTextResponse(get_flake_nix(packages_list, packages))


@api_router.get(
    "/stats/revs-per-day",
    summary="Get indexing statistics",
    description="Returns the number of nixpkgs revisions indexed per day.",
)
async def stats_revs_per_day() -> list[RevPerDay]:
    try:
        stats = await get_revs_per_day()
    except ConnectionFailure:
        raise HTTPException(status_code=503, detail="Database unavailable")

    return stats


@nix_router.get(
    "/nixpkgs/{package}",
    summary="Redirect to nixpkgs tarball",
    description="Redirects to the GitHub tarball for the nixpkgs revision containing the requested package version.",
)
async def nixpkgs(package: PackagePath) -> RedirectResponse:
    req = _parse_requirement(package)
    try:
        pkg = await get_package(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ConnectionFailure:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not pkg:
        raise HTTPException(status_code=404, detail=f"Package not found: {req.name}")

    return RedirectResponse(
        url=f"https://github.com/NixOS/nixpkgs/archive/{pkg.nixpkgs_rev.rev}.tar.gz",
        status_code=301,
    )


@api_router.get(
    "/rev/{package}",
    summary="Get nixpkgs revision info",
    description="Returns the nixpkgs revision details for a package version.",
)
async def rev(package: PackagePath) -> NixpkgsRevResponse:
    req = _parse_requirement(package)
    try:
        pkg = await get_package(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ConnectionFailure:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not pkg:
        raise HTTPException(status_code=404, detail=f"Package not found: {req.name}")

    r = pkg.nixpkgs_rev
    return NixpkgsRevResponse(rev=r.rev, hash=r.hash, date=r.date)


@nix_router.get(
    "/{packages}",
    summary="Get flake tarball",
    description="Returns a gzipped tarball containing flake.nix and flake.lock for the requested packages. Use with `nix run`.",
    response_class=StreamingResponse,
)
async def tarball(packages: PackagesPath) -> StreamingResponse:
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

    return StreamingResponse(
        get_flake_tarball(packages_list, packages),
        media_type="application/gzip",
        headers={"Content-Disposition": "attachment; filename=flake.tar.gz"},
    )


app.include_router(api_router)
app.include_router(nix_router)
