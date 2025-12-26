# wooper.dev

A web service that generates Nix flakes with specific package versions from nixpkgs history. Use any version of any nixpkgs package without writing a flake.

## Prerequisites

- [Nix](https://nixos.org/download/) with flakes enabled

## Quick Start

```bash
# Enter a shell with specific packages
nix shell "https://wooper.dev/python3;nodejs;ruff"

# Run a package directly
nix run "https://wooper.dev/uv~=0.5.0#uv" -- --version
```

The first run takes a while as Nix downloads and evaluates the flake. Subsequent runs use the cache.

## Version Specifiers

Wooper uses [PEP 440](https://peps.python.org/pep-0440/) version specifiers:

| Specifier | Meaning |
|-----------|---------|
| `uv` | Latest available version |
| `uv==0.5.0` | Exact version |
| `uv>=0.5.0` | Minimum version |
| `uv~=0.5.0` | Compatible release (>=0.5.0, <0.6.0) |

**Note:** `>` and `<` must be URL-encoded (`%3E`, `%3C`) as they break Nix's URL parser.

## Multiple Packages

Separate packages with semicolons:

```bash
nix shell "https://wooper.dev/python3~=3.12;uv~=0.5.0;ruff"
```

Wooper optimizes flake inputs by selecting the minimum number of nixpkgs revisions needed to satisfy all version requirements.

**Limits:**
- Maximum 50 packages per request
- Ambiguous packages like `python` are rejected—use `python2` or `python3`

## Portable Shell Scripts

Generate a standalone shell script that can be committed to your repo:

```bash
nix run "https://wooper.dev/uv~=0.5.0;ruff" > dev.sh
chmod +x dev.sh
```

When run, the generated flake outputs a shell script to stdout (via [quickshell](https://github.com/buurro/quickshell)). This script fetches packages directly from cache.nixos.org without re-evaluating the flake—anyone with Nix can run it instantly.

Works on aarch64-darwin, x86_64-darwin, aarch64-linux, and x86_64-linux.

## Use as a Flake Input

Pin specific package versions in your flake:

```nix
{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    wooper.url = "https://wooper.dev/uv~=0.5.0;ruff";
  };

  outputs = { self, nixpkgs, wooper }: let
    system = "aarch64-darwin";
    pkgs = nixpkgs.legacyPackages.${system};
  in {
    devShells.${system}.default = pkgs.mkShell {
      packages = [
        wooper.packages.${system}.uv
        wooper.packages.${system}.ruff
      ];
    };
  };
}
```

## API

Interactive documentation available at [wooper.dev/docs](https://wooper.dev/docs).

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /{packages}` | Flake tarball (flake.nix + flake.lock) for `nix shell`/`nix run` |
| `GET /flake/{packages}` | Raw flake.nix content |

### Example

```bash
# Get flake.nix content
curl "https://wooper.dev/flake/python3~=3.12"
```

## Development

### Setup

```bash
# Set up database connection
export WOOPER_DB="postgresql://user:password@host/database"

# Fill the packages database (use a recent date to limit initial sync)
uv run -m wooper_dev.updater --after 2025-01-01

# Start dev server
uv run uvicorn wooper_dev.main:app --reload
```

The updater scrapes [Hydra](https://hydra.nixos.org/) for successful nixpkgs builds and indexes all package versions for each revision.

### Testing

```bash
uv run pytest
```

### Update quickshell dependency

```bash
uv run scripts/update_quickshell.py
```
