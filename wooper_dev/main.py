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
    <title>Wooper - Pin nixpkgs versions easily</title>
    <script src="https://cdn.tailwindcss.com?plugins=typography"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,700;12..96,800&family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,400&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism-tomorrow.min.css" rel="stylesheet" />
    <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/prism.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-nix.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-bash.min.js"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        rose: {
                            hero: '#DDA0C8',
                            card: '#F5D0E0',
                            cardDark: '#E8B4D0',
                        },
                        gold: {
                            main: '#F5C542',
                            light: '#FAD366',
                        },
                        ink: '#1a1a1a',
                    },
                    fontFamily: {
                        display: ['Bricolage Grotesque', 'system-ui', 'sans-serif'],
                        body: ['DM Sans', 'system-ui', 'sans-serif'],
                    },
                }
            }
        }
    </script>
    <style>
        html { scroll-behavior: smooth; }
        body { font-family: 'DM Sans', system-ui, sans-serif; }

        .wave-divider {
            background: #F5C542;
            margin-top: -2px;
            line-height: 0;
        }
        .wave-divider svg {
            display: block;
            width: 100%;
            height: 60px;
        }
        .wave-divider-bottom {
            background: #DDA0C8;
            margin-top: -2px;
            line-height: 0;
        }
        .wave-divider-bottom svg {
            display: block;
            width: 100%;
            height: 60px;
        }

        /* Card icon decoration */
        .icon-box {
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 80px;
            height: 80px;
            margin-bottom: 1rem;
        }
        .icon-box::after {
            content: '';
            position: absolute;
            bottom: -8px;
            left: 50%;
            transform: translateX(-50%);
            width: 60px;
            height: 3px;
            background: repeating-linear-gradient(
                90deg,
                #1a1a1a 0px,
                #1a1a1a 8px,
                transparent 8px,
                transparent 12px
            );
        }

        /* Prism overrides */
        pre[class*="language-"], code[class*="language-"] {
            font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.875rem;
            line-height: 1.7;
        }
        pre[class*="language-"] {
            margin: 0;
            border-radius: 1rem;
        }

        /* Smooth hover transitions */
        .card-hover {
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .card-hover:hover {
            transform: translateY(-4px);
            box-shadow: 0 12px 24px -8px rgba(0,0,0,0.15);
        }

        /* Button hover effect */
        .btn-primary {
            position: relative;
            overflow: hidden;
            transition: all 0.2s ease;
        }
        .btn-primary::before {
            content: '';
            position: absolute;
            inset: 0;
            background: linear-gradient(180deg, rgba(255,255,255,0.2) 0%, transparent 50%);
            opacity: 0;
            transition: opacity 0.2s ease;
        }
        .btn-primary:hover::before {
            opacity: 1;
        }

        /* Code block styling */
        .code-window {
            background: #1a1a1a;
            border-radius: 1rem;
            overflow: hidden;
            box-shadow: 0 25px 50px -12px rgba(0,0,0,0.4);
        }
        .code-window-header {
            background: #2a2a2a;
            padding: 0.75rem 1rem;
            display: flex;
            gap: 0.5rem;
        }
        .code-window-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }

        /* Copy button feedback */
        .copy-btn {
            transition: all 0.15s ease;
        }
        .copy-btn:active {
            transform: scale(0.95);
        }
        .copy-btn.copied {
            color: #22c55e !important;
        }

        /* Try it section enhancements */
        .try-it-section {
            position: relative;
            overflow: hidden;
        }
        .try-it-section::before {
            content: '';
            position: absolute;
            inset: 0;
            background:
                radial-gradient(circle at 20% 80%, rgba(245,197,66,0.3) 0%, transparent 40%),
                radial-gradient(circle at 80% 20%, rgba(255,255,255,0.2) 0%, transparent 40%),
                radial-gradient(circle at 50% 50%, rgba(200,100,150,0.2) 0%, transparent 60%);
            animation: bgPulse 8s ease-in-out infinite;
        }
        @keyframes bgPulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.7; transform: scale(1.1); }
        }

        /* Floating decorations */
        .floating-icon {
            position: fixed;
            font-size: 2rem;
            opacity: 0.6;
            animation: float 6s ease-in-out infinite;
            pointer-events: none;
            filter: drop-shadow(0 4px 8px rgba(0,0,0,0.1));
            z-index: 1;
        }
        .floating-icon:nth-child(1) { top: 20%; left: 5%; animation-delay: 0s; }
        .floating-icon:nth-child(2) { top: 30%; right: 6%; animation-delay: 1s; animation-duration: 7s; }
        .floating-icon:nth-child(3) { top: 50%; left: 3%; animation-delay: 2s; animation-duration: 5s; }
        .floating-icon:nth-child(4) { top: 70%; right: 4%; animation-delay: 0.5s; animation-duration: 8s; }
        .floating-icon:nth-child(5) { top: 60%; left: 6%; animation-delay: 3s; animation-duration: 6s; }
        .floating-icon:nth-child(6) { top: 40%; right: 3%; animation-delay: 1.5s; animation-duration: 7s; }
        @keyframes float {
            0%, 100% { transform: translateY(0) rotate(0deg); }
            25% { transform: translateY(-15px) rotate(5deg); }
            75% { transform: translateY(10px) rotate(-5deg); }
        }

        /* Glowing card */
        .glow-card {
            position: relative;
            background: white;
            border-radius: 2rem;
            overflow: hidden;
        }
        .glow-card::before {
            content: '';
            position: absolute;
            inset: -3px;
            background: linear-gradient(135deg, #F5C542, #DDA0C8, #F5C542, #fff, #DDA0C8);
            background-size: 300% 300%;
            animation: glowRotate 4s linear infinite;
            border-radius: 2rem;
            z-index: -1;
        }
        .glow-card::after {
            content: '';
            position: absolute;
            inset: 3px;
            background: white;
            border-radius: calc(2rem - 3px);
            z-index: -1;
        }
        @keyframes glowRotate {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        /* Fancy input */
        .fancy-input {
            position: relative;
            background: linear-gradient(135deg, #fafafa 0%, #fff 100%);
            border: 2px solid transparent;
            background-clip: padding-box;
            transition: all 0.3s ease;
        }
        .fancy-input::before {
            content: '';
            position: absolute;
            inset: -2px;
            background: linear-gradient(135deg, #e0e0e0, #f0f0f0);
            border-radius: inherit;
            z-index: -1;
            transition: all 0.3s ease;
        }
        .fancy-input:focus {
            background: white;
            box-shadow: 0 0 0 4px rgba(221,160,200,0.2), 0 8px 32px -8px rgba(0,0,0,0.1);
        }
        .fancy-input:focus::before {
            background: linear-gradient(135deg, #DDA0C8, #F5C542);
        }

        /* Pulse button */
        .pulse-btn {
            position: relative;
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            transition: all 0.3s ease;
        }
        .pulse-btn::before {
            content: '';
            position: absolute;
            inset: -4px;
            background: linear-gradient(135deg, #F5C542, #DDA0C8);
            border-radius: inherit;
            opacity: 0;
            z-index: -1;
            filter: blur(12px);
            transition: opacity 0.3s ease;
        }
        .pulse-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.3);
        }
        .pulse-btn:hover::before {
            opacity: 0.6;
        }
        .pulse-btn:active {
            transform: translateY(0);
        }

        /* Terminal prompt styling */
        .terminal-line {
            position: relative;
        }
        .terminal-line::before {
            content: '$ ';
            color: #888;
        }

        /* Shimmer loading effect */
        .shimmer {
            background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);
            background-size: 200% 100%;
            animation: shimmer 1.5s infinite;
        }
        @keyframes shimmer {
            0% { background-position: 200% 0; }
            100% { background-position: -200% 0; }
        }
    </style>
</head>
<body class="bg-white text-ink antialiased">
    <!-- Navigation -->
    <nav class="bg-white/80 backdrop-blur-sm sticky top-0 z-50 px-6 py-4 flex items-center justify-between border-b border-gray-100">
        <a href="/" class="font-display font-bold text-xl tracking-tight">Wooper</a>
        <div class="flex items-center gap-8">
            <a href="#features" class="text-sm font-medium text-gray-600 hover:text-ink transition-colors">Features</a>
            <a href="#how-it-works" class="text-sm font-medium text-gray-600 hover:text-ink transition-colors">How it works</a>
            <a href="#try-it" class="text-sm font-medium text-gray-600 hover:text-ink transition-colors">Try it</a>
            <a href="/docs" class="text-sm font-medium text-gray-600 hover:text-ink transition-colors">API</a>
            <a href="https://github.com/buurro/wooper.dev" class="border-2 border-ink rounded-full px-4 py-1.5 text-sm font-semibold hover:bg-ink hover:text-white transition-all">GitHub</a>
        </div>
    </nav>

    <!-- Hero Section -->
    <section class="bg-rose-hero px-6 pt-16 pb-20 md:pt-24 md:pb-28 overflow-hidden">
        <div class="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-8 md:gap-16">
            <div class="flex-1 text-center md:text-left">
                <h1 class="font-display font-extrabold leading-[0.9] tracking-tight">
                    <span class="block text-4xl md:text-5xl text-white/90 font-normal mb-1">nix</span>
                    <span class="block text-7xl md:text-[8rem] text-white">wooper</span>
                </h1>
                <p class="font-display text-xl md:text-2xl font-bold text-ink mt-8 mb-3">Pin nixpkgs versions....easily</p>
                <p class="text-ink/70 text-lg mb-10 max-w-md">Specific package versions from nixpkgs history with a simple URL.</p>
                <a href="#try-it" class="btn-primary inline-block bg-gold-main text-ink font-semibold px-8 py-3.5 rounded-lg shadow-lg hover:shadow-xl hover:bg-gold-light">
                    Try it now
                </a>
            </div>
            <div class="flex-1 flex justify-center relative">
                <div class="text-[10rem] md:text-[14rem] leading-none select-none" style="filter: drop-shadow(0 20px 40px rgba(0,0,0,0.15));">&#129435;</div>
            </div>
        </div>
    </section>

    <!-- Wave Divider -->
    <div class="wave-divider">
        <svg viewBox="0 0 1440 60" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <path fill="#DDA0C8" d="M0,0 L1440,0 L1440,20 C1320,55 1200,40 1080,45 C960,50 840,30 720,35 C600,40 480,55 360,45 C240,35 120,50 0,40 Z"/>
        </svg>
    </div>

    <!-- Features Section -->
    <section id="features" class="bg-gold-main px-6 py-20 md:py-28">
        <div class="max-w-6xl mx-auto flex flex-col md:flex-row items-center gap-16">
            <div class="flex-1 flex justify-center">
                <div class="code-window w-full max-w-lg">
                    <div class="code-window-header">
                        <div class="code-window-dot bg-red-400"></div>
                        <div class="code-window-dot bg-yellow-400"></div>
                        <div class="code-window-dot bg-green-400"></div>
                    </div>
                    <pre class="!bg-[#1a1a1a] !rounded-none p-6 text-sm md:text-base"><code class="language-bash">nix run https://wooper.dev/python3~=3.11

nix shell https://wooper.dev/nodejs;uv

curl https://wooper.dev/flake/ruff</code></pre>
                </div>
            </div>
            <div class="flex-1">
                <h2 class="font-display text-3xl md:text-5xl font-bold text-ink leading-tight mb-6">The future of<br>Nix package pinning</h2>
                <p class="text-ink/80 text-lg leading-relaxed">Need a specific version of Python? Node.js from last year? A particular ruff release? Wooper finds the right nixpkgs revision and generates a flake for you instantly.</p>
            </div>
        </div>
    </section>

    <!-- How it Works Section -->
    <section id="how-it-works" class="bg-gold-main px-6 pb-20 md:pb-28">
        <div class="max-w-6xl mx-auto">
            <h2 class="font-display text-3xl md:text-4xl font-bold text-center text-ink mb-16">How it works</h2>
            <div class="grid md:grid-cols-3 gap-8">
                <!-- Card 1 -->
                <div class="bg-rose-card rounded-3xl p-8 card-hover">
                    <div class="icon-box">
                        <svg class="w-12 h-12" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M24 4L28 16H40L30 24L34 36L24 28L14 36L18 24L8 16H20L24 4Z" fill="#F5C542" stroke="#1a1a1a" stroke-width="2.5" stroke-linejoin="round"/>
                            <path d="M24 12V20M20 16H28" stroke="#1a1a1a" stroke-width="2.5" stroke-linecap="round"/>
                        </svg>
                    </div>
                    <h3 class="font-display text-2xl font-bold text-ink mb-3">Request</h3>
                    <p class="text-ink/70 leading-relaxed">Add packages to the URL with version specifiers. Use PEP 440 syntax like <code class="bg-rose-cardDark px-1.5 py-0.5 rounded font-mono text-sm">~=3.11</code> or <code class="bg-rose-cardDark px-1.5 py-0.5 rounded font-mono text-sm">&gt;=0.8</code></p>
                </div>
                <!-- Card 2 -->
                <div class="bg-rose-card rounded-3xl p-8 card-hover">
                    <div class="icon-box">
                        <svg class="w-12 h-12" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <circle cx="20" cy="20" r="12" fill="#F5C542" stroke="#1a1a1a" stroke-width="2.5"/>
                            <path d="M30 30L42 42" stroke="#1a1a1a" stroke-width="3" stroke-linecap="round"/>
                            <path d="M16 20H24M20 16V24" stroke="#1a1a1a" stroke-width="2.5" stroke-linecap="round"/>
                        </svg>
                    </div>
                    <h3 class="font-display text-2xl font-bold text-ink mb-3">Resolve</h3>
                    <p class="text-ink/70 leading-relaxed">Wooper searches nixpkgs history to find the exact revision with your version. We optimize for minimal revisions.</p>
                </div>
                <!-- Card 3 -->
                <div class="bg-rose-card rounded-3xl p-8 card-hover">
                    <div class="icon-box">
                        <svg class="w-12 h-12" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <rect x="8" y="12" width="32" height="28" rx="4" fill="#F5C542" stroke="#1a1a1a" stroke-width="2.5"/>
                            <path d="M16 8V16M32 8V16" stroke="#1a1a1a" stroke-width="2.5" stroke-linecap="round"/>
                            <path d="M16 24L22 30L34 18" stroke="#1a1a1a" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </div>
                    <h3 class="font-display text-2xl font-bold text-ink mb-3">Run</h3>
                    <p class="text-ink/70 leading-relaxed">Get a ready-to-use flake tarball. Works with <code class="bg-rose-cardDark px-1.5 py-0.5 rounded font-mono text-sm">nix run</code>, <code class="bg-rose-cardDark px-1.5 py-0.5 rounded font-mono text-sm">nix shell</code>, or <code class="bg-rose-cardDark px-1.5 py-0.5 rounded font-mono text-sm">nix develop</code></p>
                </div>
            </div>
        </div>
    </section>

    <!-- Wave Divider Bottom -->
    <div class="wave-divider-bottom">
        <svg viewBox="0 0 1440 60" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
            <path fill="#F5C542" d="M0,0 L1440,0 L1440,25 C1320,10 1200,40 1080,30 C960,20 840,45 720,35 C600,25 480,10 360,25 C240,40 120,15 0,30 Z"/>
        </svg>
    </div>

    <!-- Try It Section -->
    <section id="try-it" class="try-it-section bg-rose-hero px-6 py-24 md:py-32">
        <!-- Floating decorations -->
        <div class="floating-icon">&#128230;</div>
        <div class="floating-icon">&#9881;</div>
        <div class="floating-icon">&#128640;</div>
        <div class="floating-icon">&#10024;</div>
        <div class="floating-icon">&#128736;</div>
        <div class="floating-icon">&#129435;</div>

        <div class="max-w-4xl mx-auto relative z-10">
            <div class="text-center mb-12">
                <span class="inline-block px-4 py-1.5 bg-white/20 backdrop-blur-sm rounded-full text-white/90 text-sm font-medium mb-4">Interactive Demo</span>
                <h2 class="font-display text-4xl md:text-5xl font-bold text-white mb-4">Try it out</h2>
                <p class="text-white/70 text-lg max-w-lg mx-auto">Enter a package name with an optional version specifier and watch the magic happen</p>
            </div>

            <div class="glow-card p-8 md:p-10 shadow-2xl">
                <div class="flex flex-col gap-4 mb-8">
                    <label class="text-sm font-semibold text-ink/60 uppercase tracking-wider">Package Query</label>
                    <div class="flex flex-col md:flex-row gap-4">
                        <div class="flex-1 relative">
                            <span class="absolute left-5 top-1/2 -translate-y-1/2 text-ink/30 font-mono">$</span>
                            <input
                                type="text"
                                id="package-input"
                                placeholder="python3~=3.11 or nodejs;uv"
                                class="fancy-input w-full pl-10 pr-5 py-4 rounded-xl focus:outline-none text-lg font-mono"
                            />
                        </div>
                        <button
                            id="lookup-btn"
                            class="pulse-btn text-white font-semibold px-10 py-4 rounded-xl disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                        >
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                            </svg>
                            Look up
                        </button>
                    </div>
                    <p class="text-sm text-ink/50">Supports PEP 440 version specifiers: <code class="bg-gray-100 px-1.5 py-0.5 rounded">~=</code> <code class="bg-gray-100 px-1.5 py-0.5 rounded">&gt;=</code> <code class="bg-gray-100 px-1.5 py-0.5 rounded">==</code></p>
                </div>

                <!-- Results -->
                <div id="results" class="hidden">
                    <div id="error-box" class="hidden bg-red-50 border-2 border-red-300 text-red-700 px-5 py-4 rounded-xl mb-6 font-medium flex items-center gap-3">
                        <svg class="w-5 h-5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/></svg>
                        <span id="error-text"></span>
                    </div>

                    <div id="success-box" class="hidden space-y-6">
                        <!-- Revision Info Card -->
                        <div class="bg-gradient-to-br from-rose-card/60 to-rose-card/30 rounded-2xl p-6 border border-rose-cardDark/30">
                            <div class="flex items-center gap-2 mb-4">
                                <div class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                                <h3 class="font-display font-bold text-ink">Found Nixpkgs Revision</h3>
                            </div>
                            <div class="grid md:grid-cols-3 gap-4">
                                <div class="bg-white/80 backdrop-blur-sm rounded-xl p-4 border border-white">
                                    <span class="text-xs font-semibold text-ink/50 uppercase tracking-wider">Commit</span>
                                    <code id="result-rev" class="block text-ink font-mono text-sm mt-1 truncate"></code>
                                </div>
                                <div class="bg-white/80 backdrop-blur-sm rounded-xl p-4 border border-white">
                                    <span class="text-xs font-semibold text-ink/50 uppercase tracking-wider">Hash</span>
                                    <code id="result-hash" class="block text-ink font-mono text-sm mt-1 truncate"></code>
                                </div>
                                <div class="bg-white/80 backdrop-blur-sm rounded-xl p-4 border border-white">
                                    <span class="text-xs font-semibold text-ink/50 uppercase tracking-wider">Date</span>
                                    <code id="result-date" class="block text-ink font-mono text-sm mt-1"></code>
                                </div>
                            </div>
                        </div>

                        <!-- Commands -->
                        <div>
                            <h3 class="font-display font-bold text-ink mb-4 flex items-center gap-2">
                                <svg class="w-5 h-5 text-gold-main" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M2 5a2 2 0 012-2h12a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V5zm3.293 1.293a1 1 0 011.414 0l3 3a1 1 0 010 1.414l-3 3a1 1 0 01-1.414-1.414L7.586 10 5.293 7.707a1 1 0 010-1.414zM11 12a1 1 0 100 2h3a1 1 0 100-2h-3z" clip-rule="evenodd"/></svg>
                                Quick Commands
                            </h3>
                            <div class="space-y-3">
                                <div class="group flex items-center bg-gradient-to-r from-ink to-gray-800 rounded-xl overflow-hidden shadow-lg hover:shadow-xl transition-shadow">
                                    <div class="flex-1 flex items-center px-5 py-4 overflow-x-auto">
                                        <span class="text-gray-500 font-mono mr-2 select-none">$</span>
                                        <code id="cmd-run" class="text-green-400 text-sm font-mono whitespace-nowrap"></code>
                                    </div>
                                    <button onclick="copyToClipboard('cmd-run', this)" class="copy-btn text-gray-500 hover:text-white hover:bg-white/10 p-4 transition-all" title="Copy to clipboard">
                                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                                    </button>
                                </div>
                                <div class="group flex items-center bg-gradient-to-r from-ink to-gray-800 rounded-xl overflow-hidden shadow-lg hover:shadow-xl transition-shadow">
                                    <div class="flex-1 flex items-center px-5 py-4 overflow-x-auto">
                                        <span class="text-gray-500 font-mono mr-2 select-none">$</span>
                                        <code id="cmd-shell" class="text-green-400 text-sm font-mono whitespace-nowrap"></code>
                                    </div>
                                    <button onclick="copyToClipboard('cmd-shell', this)" class="copy-btn text-gray-500 hover:text-white hover:bg-white/10 p-4 transition-all" title="Copy to clipboard">
                                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                                    </button>
                                </div>
                            </div>
                        </div>

                        <!-- Flake Preview -->
                        <div>
                            <h3 class="font-display font-bold text-ink mb-4 flex items-center gap-2">
                                <svg class="w-5 h-5 text-gold-main" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clip-rule="evenodd"/></svg>
                                Generated Flake
                            </h3>
                            <div class="code-window">
                                <div class="code-window-header items-center">
                                    <div class="code-window-dot bg-[#ff5f57]"></div>
                                    <div class="code-window-dot bg-[#febc2e]"></div>
                                    <div class="code-window-dot bg-[#28c840]"></div>
                                    <span class="ml-4 text-gray-500 text-sm font-mono">flake.nix</span>
                                    <button onclick="copyFlake()" class="copy-btn ml-auto text-gray-500 hover:text-white transition-colors text-sm flex items-center gap-1.5">
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                                        Copy
                                    </button>
                                </div>
                                <pre class="!bg-[#1a1a1a] !rounded-none !p-5 text-sm overflow-x-auto"><code id="flake-content" class="language-nix"></code></pre>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Loading -->
                <div id="loading" class="hidden text-center py-16">
                    <div class="relative inline-flex">
                        <div class="w-16 h-16 rounded-full border-4 border-rose-card"></div>
                        <div class="absolute inset-0 w-16 h-16 rounded-full border-4 border-transparent border-t-rose-hero animate-spin"></div>
                    </div>
                    <p class="mt-6 text-ink/60 font-medium">Searching nixpkgs history...</p>
                    <p class="mt-2 text-ink/40 text-sm">This may take a moment</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Footer -->
    <footer class="bg-ink text-white px-6 py-12">
        <div class="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
            <div class="flex items-center gap-3">
                <span class="text-3xl">&#129435;</span>
                <span class="font-display font-bold text-xl">Wooper</span>
            </div>
            <div class="flex gap-8 text-sm text-gray-400">
                <a href="/docs" class="hover:text-white transition-colors">API Documentation</a>
                <a href="https://github.com/buurro/wooper.dev" class="hover:text-white transition-colors">GitHub</a>
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

        function copyToClipboard(elementId, btn) {
            const text = document.getElementById(elementId).textContent;
            navigator.clipboard.writeText(text);
            btn.classList.add('copied');
            setTimeout(() => btn.classList.remove('copied'), 1500);
        }

        function copyFlake() {
            const text = document.getElementById('flake-content').textContent;
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
                document.getElementById('error-text').textContent = err.message;
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
