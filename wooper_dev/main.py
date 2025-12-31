from importlib.metadata import PackageNotFoundError, version
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Path
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
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
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wooper</title>
    <script src="https://cdn.tailwindcss.com?plugins=typography"></script>
    <link href="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism-tomorrow.min.css" rel="stylesheet" />
    <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/prism.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-nix.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-bash.min.js"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        pink: { 500: '#d946ef', 600: '#c026d3' },
                        yellow: { 400: '#facc15', 500: '#eab308' },
                    }
                }
            }
        }
    </script>
    <style>
        .wave-divider {
            background: #facc15;
            margin-top: -1px;
        }
        .wave-divider svg {
            display: block;
            width: 100%;
            height: 50px;
        }
        /* Prism overrides */
        pre[class*="language-"], code[class*="language-"] {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.875rem;
            line-height: 1.7;
        }
        pre[class*="language-"] {
            margin: 0;
        }
    </style>
</head>
<body class="bg-white text-gray-900 font-sans">
    <!-- Navigation -->
    <nav class="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div class="font-bold text-lg">Wooper</div>
        <div class="flex items-center gap-6">
            <a href="#features" class="text-sm text-gray-700 hover:text-gray-900">Features</a>
            <a href="#how-it-works" class="text-sm text-gray-700 hover:text-gray-900">How it works</a>
            <a href="#try-it" class="text-sm text-gray-700 hover:text-gray-900">Try it</a>
            <a href="/docs" class="text-sm text-gray-700 hover:text-gray-900">API Docs</a>
            <a href="https://github.com/buurro/wooper.dev" class="border border-gray-900 rounded px-3 py-1 text-sm hover:bg-gray-100">GitHub</a>
        </div>
    </nav>

    <!-- Hero Section -->
    <section class="bg-fuchsia-500 text-white px-6 py-16 md:py-24">
        <div class="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-12">
            <div class="flex-1">
                <h1 class="text-6xl md:text-8xl font-bold leading-none mb-2">
                    <span class="text-5xl md:text-6xl font-light block">nix</span>
                    wooper
                </h1>
                <p class="text-2xl md:text-3xl font-semibold mt-6 mb-2">Pin nixpkgs versions....easily</p>
                <p class="text-lg opacity-90 mb-8">Specific package versions with a simple URL.</p>
                <a href="#how-it-works" class="inline-block bg-yellow-400 text-gray-900 font-semibold px-6 py-3 rounded hover:bg-yellow-300 transition">Get Started</a>
            </div>
            <div class="flex-1 flex justify-center">
                <div class="text-[12rem] leading-none">&#129435;</div>
            </div>
        </div>
    </section>

    <!-- Wave Divider -->
    <div class="wave-divider">
        <svg viewBox="0 0 1440 50" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <path fill="#d946ef" d="M0,0 L1440,0 L1440,25 Q1080,50 720,25 Q360,0 0,25 Z"/>
        </svg>
    </div>

    <!-- Features Section -->
    <section id="features" class="bg-yellow-400 px-6 py-16 md:py-24">
        <div class="max-w-6xl mx-auto flex flex-col md:flex-row items-center gap-12">
            <div class="flex-1 flex justify-center">
                <pre class="!bg-gray-900 p-6 rounded-2xl text-sm md:text-base overflow-x-auto shadow-lg"><code class="language-bash">nix run https://wooper.dev/python3~=3.11

nix shell https://wooper.dev/nodejs;uv

curl https://wooper.dev/flake/ruff</code></pre>
            </div>
            <div class="flex-1">
                <h2 class="text-3xl md:text-4xl font-bold text-gray-900 mb-4">The future of<br>Nix package pinning</h2>
                <p class="text-gray-800 text-lg">Need a specific version of Python? Node.js from last year? A particular ruff release? Wooper finds the right nixpkgs revision and generates a flake for you instantly.</p>
            </div>
        </div>
    </section>

    <!-- How it Works Section -->
    <section id="how-it-works" class="bg-yellow-400 px-6 pb-16 md:pb-24">
        <div class="max-w-6xl mx-auto">
            <h2 class="text-3xl md:text-4xl font-bold text-center text-gray-900 mb-12">How it works</h2>
            <div class="grid md:grid-cols-3 gap-6">
                <!-- Card 1 -->
                <div class="bg-pink-100 rounded-2xl p-6">
                    <div class="text-5xl mb-4">⚡</div>
                    <h3 class="text-xl font-bold text-gray-900 mb-2">Request</h3>
                    <p class="text-gray-700">Just add your packages to the URL with optional version specifiers. Use PEP 440 syntax like <code class="bg-pink-200 px-1 rounded">~=3.11</code> or <code class="bg-pink-200 px-1 rounded">>=0.8.0</code>.</p>
                </div>
                <!-- Card 2 -->
                <div class="bg-pink-100 rounded-2xl p-6">
                    <div class="text-5xl mb-4">🔍</div>
                    <h3 class="text-xl font-bold text-gray-900 mb-2">Resolve</h3>
                    <p class="text-gray-700">Wooper searches through nixpkgs history to find the exact revision containing your requested version. We optimize for minimal revisions.</p>
                </div>
                <!-- Card 3 -->
                <div class="bg-pink-100 rounded-2xl p-6">
                    <div class="text-5xl mb-4">📦</div>
                    <h3 class="text-xl font-bold text-gray-900 mb-2">Run</h3>
                    <p class="text-gray-700">Get a ready-to-use flake tarball. Works directly with <code class="bg-pink-200 px-1 rounded">nix run</code>, <code class="bg-pink-200 px-1 rounded">nix shell</code>, or <code class="bg-pink-200 px-1 rounded">nix develop</code>.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Try It Section -->
    <section id="try-it" class="bg-fuchsia-500 px-6 py-16 md:py-24">
        <div class="max-w-4xl mx-auto">
            <h2 class="text-3xl md:text-4xl font-bold text-center text-white mb-4">Try it out</h2>
            <p class="text-center text-white/80 mb-8">Enter a package name with an optional version specifier</p>

            <div class="bg-white rounded-2xl p-6 md:p-8 shadow-xl">
                <div class="flex flex-col md:flex-row gap-4 mb-6">
                    <input
                        type="text"
                        id="package-input"
                        placeholder="e.g. python3~=3.11 or nodejs;uv"
                        class="flex-1 px-4 py-3 border-2 border-gray-200 rounded-lg focus:border-fuchsia-500 focus:outline-none text-lg"
                    />
                    <button
                        id="lookup-btn"
                        class="bg-fuchsia-500 text-white font-semibold px-8 py-3 rounded-lg hover:bg-fuchsia-600 transition disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        Look up
                    </button>
                </div>

                <!-- Results -->
                <div id="results" class="hidden">
                    <div id="error-box" class="hidden bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-4"></div>

                    <div id="success-box" class="hidden">
                        <div class="bg-gray-50 rounded-lg p-4 mb-4">
                            <h3 class="font-semibold text-gray-700 mb-2">Nixpkgs Revision</h3>
                            <div class="grid md:grid-cols-3 gap-4 text-sm">
                                <div>
                                    <span class="text-gray-500">Commit:</span>
                                    <code id="result-rev" class="block bg-gray-200 px-2 py-1 rounded mt-1 truncate"></code>
                                </div>
                                <div>
                                    <span class="text-gray-500">Hash:</span>
                                    <code id="result-hash" class="block bg-gray-200 px-2 py-1 rounded mt-1 truncate"></code>
                                </div>
                                <div>
                                    <span class="text-gray-500">Date:</span>
                                    <code id="result-date" class="block bg-gray-200 px-2 py-1 rounded mt-1"></code>
                                </div>
                            </div>
                        </div>

                        <h3 class="font-semibold text-gray-700 mb-2">Use it</h3>
                        <div class="space-y-2">
                            <div class="flex items-center gap-2">
                                <code id="cmd-run" class="flex-1 bg-gray-900 text-green-400 px-4 py-2 rounded-lg text-sm overflow-x-auto"></code>
                                <button onclick="copyToClipboard('cmd-run')" class="text-gray-500 hover:text-gray-700 p-2" title="Copy">
                                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                                </button>
                            </div>
                            <div class="flex items-center gap-2">
                                <code id="cmd-shell" class="flex-1 bg-gray-900 text-green-400 px-4 py-2 rounded-lg text-sm overflow-x-auto"></code>
                                <button onclick="copyToClipboard('cmd-shell')" class="text-gray-500 hover:text-gray-700 p-2" title="Copy">
                                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                                </button>
                            </div>
                        </div>

                        <div class="mt-4">
                            <h3 class="font-semibold text-gray-700 mb-2">flake.nix</h3>
                            <pre class="!bg-gray-900 !p-4 rounded-lg text-sm overflow-x-auto"><code id="flake-content" class="language-nix"></code></pre>
                        </div>
                    </div>
                </div>

                <!-- Loading -->
                <div id="loading" class="hidden text-center py-8">
                    <div class="inline-block animate-spin rounded-full h-8 w-8 border-4 border-fuchsia-500 border-t-transparent"></div>
                    <p class="mt-2 text-gray-600">Looking up package...</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Footer -->
    <footer class="bg-gray-900 text-white px-6 py-8">
        <div class="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
            <div class="font-bold">Wooper</div>
            <div class="flex gap-6 text-sm text-gray-400">
                <a href="/docs" class="hover:text-white">API Docs</a>
                <a href="https://github.com/buurro/wooper.dev" class="hover:text-white">GitHub</a>
            </div>
        </div>
    </footer>
    <script>
        const packageInput = document.getElementById('package-input');
        const lookupBtn = document.getElementById('lookup-btn');
        const results = document.getElementById('results');
        const errorBox = document.getElementById('error-box');
        const successBox = document.getElementById('success-box');
        const loading = document.getElementById('loading');

        function copyToClipboard(elementId) {
            const text = document.getElementById(elementId).textContent;
            navigator.clipboard.writeText(text);
        }

        async function lookup() {
            const pkg = packageInput.value.trim();
            if (!pkg) return;

            // Reset UI
            results.classList.add('hidden');
            errorBox.classList.add('hidden');
            successBox.classList.add('hidden');
            loading.classList.remove('hidden');
            lookupBtn.disabled = true;

            try {
                // Fetch revision info for first package (for display)
                const firstPkg = pkg.split(';')[0];
                const revResponse = await fetch(`/api/rev/${encodeURIComponent(firstPkg)}`);

                if (!revResponse.ok) {
                    const error = await revResponse.json();
                    throw new Error(error.detail || 'Failed to look up package');
                }

                const revData = await revResponse.json();

                // Fetch flake.nix content
                const flakeResponse = await fetch(`/flake/${encodeURIComponent(pkg)}`);
                const flakeContent = flakeResponse.ok ? await flakeResponse.text() : 'Failed to load flake.nix';

                // Update UI
                document.getElementById('result-rev').textContent = revData.rev;
                document.getElementById('result-hash').textContent = revData.hash;
                document.getElementById('result-date').textContent = new Date(revData.date * 1000).toLocaleDateString();

                const encodedPkg = encodeURIComponent(pkg);
                document.getElementById('cmd-run').textContent = `nix run https://wooper.dev/${encodedPkg}`;
                document.getElementById('cmd-shell').textContent = `nix shell https://wooper.dev/${encodedPkg}`;

                const flakeEl = document.getElementById('flake-content');
                flakeEl.textContent = flakeContent;
                Prism.highlightElement(flakeEl);

                successBox.classList.remove('hidden');
            } catch (err) {
                errorBox.textContent = err.message;
                errorBox.classList.remove('hidden');
            } finally {
                loading.classList.add('hidden');
                results.classList.remove('hidden');
                lookupBtn.disabled = false;
            }
        }

        lookupBtn.addEventListener('click', lookup);
        packageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') lookup();
        });
    </script>
</body>
</html>
"""
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
