# Security review: FloorplanToBlender3d

- **Upstream:** https://github.com/grebtsew/FloorplanToBlender3d (GPL-3.0, 646★, last push 2024-10-09)
- **Reviewed commit:** `2da4c9532e9866b025c67aee17f87ff7e0b8db98` (master tip, 2026-11-21)
- **Method:** shallow quarantine clone; nothing installed or executed. Scanners: semgrep (`p/python`,
  `p/secrets`, `p/dockerfile`), bandit `-ll`, gitleaks, osv-scanner, trivy image. Manual read of the
  scanner hits and the run path (`main.py`, `FloorplanToBlenderLib/`, `Blender/floorplan_to_3dObject_in_blender.py`,
  `Blender/blender_export_any.py`; `Testing/` for setup step 2).
- **Rule:** only findings on the run path (library → headless Blender) block; everything else is noted.

## Verdict

| Part | Verdict |
|---|---|
| Source as a pinned submodule | **GO with fixes** (F1–F4 below) |
| Upstream `requirements.txt` pins | **NO-GO**: install the run-path deps at current versions instead (F1) |
| Prebuilt image `grebtsew/floorplan-to-blender` | **NO-GO**: built 2022-03-09, older than the pin; Ubuntu 18.04 (EOL); 13 CRITICAL, 224 HIGH |

## Clean checks

- No `.claude/`, `.mcp.json`, `CLAUDE.md`, `AGENTS.md`, `.vscode/`, `.envrc`, `.devcontainer/`, `conftest.py`
  or `*.pth`. No `setup.py`, `setup.cfg` or `pyproject.toml`, so installing never runs repo code. No `.blend`
  files, so no embedded autorun scripts. The one workflow (`docker-build.yml`) only builds the image.
- gitleaks: no leaks. On the run path and in `Testing/`: no network, `pickle`, `marshal`, `base64`,
  `importlib`, `os.system` or `popen`, and no obfuscation.
- Blender is launched with `check_output([...])` list args (no shell). The Blender script reads its data
  with `json.loads`.
- Writes are **not** confined to the CWD. `./Configs/` and `Data/` are relative to the CWD, but `main.py:81`
  passes the upstream checkout as `program_path`, so Blender saves the `.blend` file and reads `Data/` inside
  the submodule (`floorplan_to_3dObject_in_blender.py:163`).

## Run-path findings and their fixes

| # | Finding | Fix in floorplan3d |
|---|---|---|
| F1 | 2022 pins with known CVEs: pillow 9.0.1 (51 advisory groups, max CVSS 9.8), opencv-python 4.2.0.34 (GHSA-qr4w-53vh-m672, 8.8), setuptools 54.1.0 (direct pin), and urllib3/idna (transitive, via requests) | `uv add opencv-python numpy scipy pillow matplotlib pyfiglet` at current versions. Skip `configparser` and `future-fstrings` (stdlib / not needed on 3.12), `requests` (server only), and `black`/`pytest`/`setuptools` (dev only). |
| F2 | `FloorplanToBlenderLib/stacking.py:63,65` passes each stacking-file line to `eval()` | `convert.py` never uses stacking mode, and is never given a stacking file someone else wrote. |
| F3 | `IO.clean_data_folder("Data/")` recursively deletes `./Data` under the CWD (`IO.py:166-170`). The Blender script paths are also CWD-relative (`const.py:43`, `main.py:58`). | `convert.py` sets the CWD to a scratch dir under `work/`, passes absolute paths to upstream's `Blender/*.py`, and passes the scratch dir as `program_path`. |
| F4 | `IO.blender_installed()` returns the first file named `blender` anywhere under `/` (`IO.py:47-60`), and `main.py:91-100` offers it as the default and then runs it | Pass the Blender path explicitly; never go through `main.py`'s `input()` dialog. |

## Off-path notes (not used here, don't enable)

- `Server/api/server.py:100-160` dispatches `getattr(rmi, kwargs["func"])` from requests with no
  authentication, and the Docker image exposes ports 80/8000/8001.
- Docker `jupyter` mode starts Jupyter on `0.0.0.0` with `--NotebookApp.token=''` and `--allow-root`.
- `Server/test/test_client.py:81,158` calls `eval()` on HTTP response text. The vendored swagger-ui JS
  (~3.3 MB) is served only by the server. bandit could not parse `Server/flask/flask_handler.py`.
- `Dockerfile`: no `USER` (runs as root), base image is the EOL `ubuntu:18.04`, the Blender tarball is
  fetched with `curl` and not checksum-verified, and Python comes from the deadsnakes PPA.

## Limits

- Static review only. A well-hidden payload in ordinary-looking source would get past it. The protection
  that actually holds is isolation: `scripts/sandbox.py` (bwrap `--unshare-all`, so no network; `--clearenv`;
  repo and Python read-only, only named scratch dirs writable). Sessions run on WSL directly, not in a
  devcontainer, so nothing else confines upstream code that runs outside it.
- Enforced by `scripts/upstream_guard.py` (glue scripts exit on a writable repo) and the Bash hook
  `scripts/claude_upstream_hook.py`. Tripwire: `find vendor -name '*.pyc'` prints nothing; `make test` fails otherwise.

## Re-run on every pin bump

```bash
PIN=<new full sha>; Q=$(mktemp -d)/ftb
git init -q "$Q" && git -C "$Q" -c core.hooksPath=/dev/null fetch -q --depth 1 https://github.com/grebtsew/FloorplanToBlender3d "$PIN" \
  && git -C "$Q" -c core.hooksPath=/dev/null checkout -q FETCH_HEAD && git -C "$Q" rev-parse HEAD
find "$Q" \( -name .mcp.json -o -name .claude -o -name CLAUDE.md -o -name AGENTS.md -o -name .vscode -o -name .envrc \
  -o -name .devcontainer -o -name '*.blend' -o -name '*.pth' -o -name setup.py -o -name setup.cfg -o -name pyproject.toml \
  -o -name conftest.py \) -print; ls "$Q/.github/workflows"
grep -nE 'eval\(|exec\(|compile\(|__import__|importlib|marshal|pickle|base64|subprocess|os\.system|popen|requests\.|urllib|socket|http\.client|ftplib|rmtree|unlink|os\.remove' \
  "$Q"/main.py "$Q"/FloorplanToBlenderLib/*.py "$Q"/Blender/*.py "$Q"/Testing/*.py "$Q"/Testing/*/*.py
gitleaks dir "$Q" --no-banner --redact
bandit -q -r "$Q" -ll -f json | jq -r '[.results[].test_id]|group_by(.)|map("\(.[0])=\(length)")|join(" ")'
semgrep scan --metrics=off --config p/python --config p/secrets --config p/dockerfile -q "$Q"
```

Expected at `2da4c95`:

- `rev-parse` prints the pin. `find` prints nothing, and the only workflow is `docker-build.yml`.
- grep matches only `main.py:1`, `stacking.py:63,65`, `IO.py:168,170` and `floorplan_to_3dObject_in_blender.py:143`
  (a bpy `do_unlink`). gitleaks finds no leaks.
- bandit prints `B113=29 B307=4`.
- semgrep reports exactly 3 findings: `Dockerfile:80`, `Server/shared_variables.py:131` and
  `Server/swagger/json_generator.py:25`.

bandit and semgrep already exit non-zero at the baseline, so compare their output to this list instead of
relying on the exit code. Read anything new before you bump the pin.
