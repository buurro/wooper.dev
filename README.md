# wooper.dev

Use any version of any nixpkgs package without writing a flake.

## Usage

```bash
# Enter a shell with specific package versions
nix shell "https://wooper.dev/python3;nodejs;ruff"

# Run a package directly
nix run "https://wooper.dev/uv~=0.5.0#uv" -- --version
```

The first run takes a while as Nix downloads and evaluates the flake.

### Portable shell scripts

Generate a standalone shell script that can be committed to your repo:

```bash
nix run "https://wooper.dev/uv~=0.5.0;ruff" > dev.sh
chmod +x dev.sh
```

The script fetches packages directly from cache.nixos.org without re-evaluating the flake. Anyone with Nix can run it instantly.

Works on aarch64-darwin, x86_64-darwin, aarch64-linux, and x86_64-linux.

### Version specifiers

Wooper uses [PEP 440](https://peps.python.org/pep-0440/) version specifiers:

| Specifier | Meaning |
|-----------|---------|
| `uv` | Latest available version |
| `uv==0.5.0` | Exact version |
| `uv>=0.5.0` | Minimum version |
| `uv~=0.5.0` | Compatible release (>=0.5.0, <0.6.0) |

### Multiple packages

Separate packages with semicolons:

```bash
nix shell "https://wooper.dev/python3~=3.12;uv~=0.5.0;ruff"
```

Note: `>` and `<` must be URL-encoded (`%3E`, `%3C`) as they break Nix's URL parser.

## API

See [wooper.dev/docs](https://wooper.dev/docs) for all endpoints.

## Development

```bash
export WOOPER_DB="postgresql://user:password@host/database"

# Fill the packages database (use a recent date)
uv run -m wooper_dev.updater --after 2025-01-01

# Start dev server
uv run uvicorn wooper_dev.main:app --reload

# Update quickshell dependency
uv run scripts/update_quickshell.py
```
