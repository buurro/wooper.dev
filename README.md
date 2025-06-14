# wooper.dev

### Usage

```bash
nix shell "https://wooper.dev/uv~=0.6.0;ruff==0.11.10"
```

### Development

Set the `WOOPER_DB` environment variable to a postgres database connection string e.g. `"postgresql://user:password@host/database"`

Run the updater function to fill the packages database:

```bash
uv run -m wooper_dev.updater --after 2025-06-13
```

Run the web server:

```bash
uv run uvicorn wooper_dev.main:app --reload
```
