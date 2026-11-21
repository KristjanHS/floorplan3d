# Security review: FloorplanToBlender3d

- **Upstream:** https://github.com/grebtsew/FloorplanToBlender3d (GPL-3.0, 646★, last push 2024-10-09)
- **Reviewed commit:** `2da4c9532e9866b025c67aee17f87ff7e0b8db98` (master tip, 2026-11-21)
- **Method:** shallow quarantine clone; nothing installed or executed. Scanners: semgrep (`p/python`,
  `p/secrets`, `p/dockerfile`), bandit `-ll`, gitleaks, osv-scanner, trivy image. Manual read of the
  scanner hits and the run path (`main.py`, `FloorplanToBlenderLib/`, `Blender/floorplan_to_3dObject_in_blender.py`).
- **Rule:** only findings on the run path (library → headless Blender) block; everything else is noted.

## Verdict

| Part | Verdict |
|---|---|
| Source as a pinned submodule | **GO with fixes** (F1–F4 below) |
| Upstream `requirements.txt` pins | **NO-GO**: install the run-path deps at current versions instead (F1) |
| Prebuilt image `grebtsew/floorplan-to-blender` | **NO-GO**: built 2022-03-09, older than the pin; Ubuntu 18.04; 13 CRITICAL, 224 HIGH |

## Clean checks

- No `.claude/`, `.mcp.json`, `CLAUDE.md`, `AGENTS.md`, `.vscode/` or `.envrc`. No `setup.py` or
  `pyproject.toml`, so installing never runs repo code. No `.blend` files, so no embedded autorun scripts.
- gitleaks: no leaks. No network, `pickle`, `marshal`, `base64`, `importlib` or obfuscation on the run path.
- Blender is launched with `check_output([...])` list args (no shell). The Blender script reads `Data/*.txt`
  with `json.loads`. Every run-path write is relative to the CWD (`./Configs/`, `Data/`).
- CI (`.github/workflows/docker-build.yml`) only builds the Docker image.

## Run-path findings and their fixes

| # | Finding | Fix in floorplan3d |
|---|---|---|
| F1 | Pins from 2022 carry known CVEs: pillow 9.0.1 (49 advisories, max CVSS 9.8), opencv-python 4.2.0.34 (GHSA-qr4w-53vh-m672, 8.8), urllib3/idna/setuptools transitively | `uv add opencv-python numpy scipy pillow matplotlib pyfiglet` at current versions. Skip `configparser` and `future-fstrings` (stdlib / not needed on 3.12), `requests` (server only), and `black`/`pytest`/`setuptools` (dev only). |
| F2 | `FloorplanToBlenderLib/stacking.py:63,65` passes each stacking-file line to `eval()` | `convert.py` never uses stacking mode, and is never given a stacking file someone else wrote. |
| F3 | `IO.clean_data_folder("Data/")` recursively deletes `./Data` under the CWD | `convert.py` runs upstream with the CWD set to a dedicated scratch dir under `work/`. |
| F4 | `IO.blender_installed()` searches all of `/` for `blender`, which includes `/mnt/c` under WSL | Pass the Blender path explicitly; never go through `main.py`'s `input()` dialog. |

## Off-path notes (not used here, don't enable)

- `Server/api/server.py:100-160` dispatches `getattr(rmi, kwargs["func"])` from requests with no
  authentication, and the Docker image exposes ports 80/8000/8001.
- Docker `jupyter` mode starts Jupyter on `0.0.0.0` with `--NotebookApp.token=''` and `--allow-root`.
- `Server/test/test_client.py:81,158` calls `eval()` on HTTP response text. The vendored swagger-ui JS
  (~3.4 MB) is served only by the server.
- `Dockerfile`: runs as root, base image is the EOL `ubuntu:18.04`, the Blender tarball is fetched with
  `curl` and not checksum-verified, and Python comes from the deadsnakes PPA.

## Limits

- Static review only. A well-hidden payload in ordinary-looking source would get past it. The protection
  that actually holds is isolation: an egress-firewalled sandbox.

## Re-run on every pin bump

```bash
Q=$(mktemp -d) && git -c core.hooksPath=/dev/null clone -q --depth 1 https://github.com/grebtsew/FloorplanToBlender3d "$Q/ftb"
find "$Q/ftb" \( -name .mcp.json -o -name .claude -o -name CLAUDE.md -o -name AGENTS.md -o -name .vscode -o -name '*.blend' -o -name setup.py -o -name pyproject.toml \) -print
gitleaks dir "$Q/ftb" --no-banner --redact && bandit -q -r "$Q/ftb" -ll
semgrep scan --metrics=off --config p/python --config p/secrets "$Q/ftb" --error
grep -nE 'eval\(|exec\(|pickle|base64|subprocess|requests\.|urllib|socket' "$Q"/ftb/main.py "$Q"/ftb/FloorplanToBlenderLib/*.py "$Q"/ftb/Blender/*.py
```

Expected at `2da4c95`: `find` prints nothing; bandit lists only B307 (`stacking.py`, `test_client.py`) and
B113; the grep matches only `main.py:1` (`check_output`) and `stacking.py:63,65`. Anything new needs a read
before you bump the pin.
