# A stale uv.lock fails every uv command instead of being silently rewritten mid-gate.
export UV_LOCKED := 1
# Empty tree: diff --check against it covers every tracked file, committed or not.
EMPTY_TREE := 4b825dc642cb6eb9a060e54bf8d69288fbee4904

BLENDER ?= blender
UPSTREAM := vendor/FloorplanToBlender3d
UT := work/upstream-test

.PHONY: help sync check whitespace lint fmt test audit push-gate setup-hooks pre-commit upstream-test preprocess convert export

help:
	@echo "make sync     - uv sync (locked, all groups) + uv pip check"
	@echo "make check    - verification gate (syncwt runs it): whitespace + lint + test"
	@echo "make lint     - ruff check + ruff format --check + pyright"
	@echo "make fmt      - ruff check --fix + ruff format (rewrites files)"
	@echo "make test     - pytest (skips while tests/ is absent)"
	@echo "make preprocess - PDFs in plans.yaml -> work/<floor>.png + _grid.png + .json"
	@echo "make convert    - sandboxed upstream run per floor -> work/<floor>/<floor>_raw.blend"
	@echo "make export     - Blender: scale raw blend -> output/<floor>/ .blend .glb .obj previews + wall check"
	@echo "make upstream-test - upstream's own tests, sandboxed (no display tests)"
	@echo "make audit    - uv audit: known vulnerabilities in uv.lock"
	@echo "make setup-hooks - install pre-commit (commit) + make check (push) git hooks"
	@echo "make pre-commit  - run all commit-stage hooks on all files"
	@echo "make push-gate   - pre-push: clean tree + pushed ref == HEAD, then check (one progress line per step)"
	@echo "make syncwt|commit|commitwt - shared worktree targets (~/.config/make/worktree.mk)"

sync:
	uv sync --locked --all-groups
	uv pip check

# One step list for check (syncwt) and push-gate; run in order, stopping at the first failure.
CHECK_STEPS := whitespace lint test

check:
	@for s in $(CHECK_STEPS); do $(MAKE) --no-print-directory $$s || exit; done

whitespace:
	@git diff --check $(EMPTY_TREE) -- . ':!*.md'

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

fmt:
	uv run ruff check --fix .
	uv run ruff format .

test:
	@if [ -d tests ]; then uv run pytest -q || [ $$? -eq 5 ]; else echo "pytest: no tests/ yet"; fi

preprocess:
	uv run python scripts/preprocess.py

convert:
	uv run python scripts/convert.py --blender $(BLENDER)

export:
	uv run python scripts/export.py --blender $(BLENDER)

# Upstream tests write ../Configs relative to Testing/, so run a scratch copy; Images/ is linked read-only.
upstream-test:
	rm -rf $(UT) && mkdir -p $(UT)
	cp -r $(UPSTREAM)/Testing $(UPSTREAM)/FloorplanToBlenderLib $(UT)/
	ln -s $(abspath $(UPSTREAM)/Images) $(UT)/Images
	# test_config reads ../Configs, which upstream only creates on a first run from the repo root.
	uv run python scripts/sandbox.py --rw $(UT) --chdir $(UT) -- \
	  $(abspath .venv/bin/python) -c 'from FloorplanToBlenderLib import config; config.generate_file()'
	uv run python scripts/sandbox.py --rw $(UT) --env MPLBACKEND=Agg --chdir $(UT)/Testing -- \
	  $(abspath .venv/bin/python) -m pytest -q -p no:cacheprovider -c /dev/null --rootdir=. --ignore=visuals

audit:
	uv audit

# Pre-push gate. `check` reads the working tree, so require it to BE the pushed commit:
# clean (untracked included) and the pushed ref == HEAD (PRE_COMMIT_TO_REF set by pre-commit).
push-gate:
	@test -z "$$(git status --porcelain)" || { echo "push-gate: commit or stash first — check gates the working tree" >&2; exit 1; }
	@test -z "$${PRE_COMMIT_TO_REF:-}" || test "$$(git rev-parse "$$PRE_COMMIT_TO_REF")" = "$$(git rev-parse HEAD)" \
	  || { echo "push-gate: push the checked-out branch, or check it out first" >&2; exit 1; }
	+@MAKE='$(MAKE)' scripts/gate_steps.sh $(CHECK_STEPS)

setup-hooks:
	uv run pre-commit install

pre-commit:
	uv run pre-commit run --all-files

# Bottom, so the include can't steal the default goal; `-include` so clones without dotfiles still work.
-include $(HOME)/.config/make/worktree.mk
