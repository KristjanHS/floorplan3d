# A stale uv.lock fails every uv command instead of being silently rewritten mid-gate.
export UV_LOCKED := 1
# Empty tree: diff --check against it covers every tracked file, committed or not.
EMPTY_TREE := 4b825dc642cb6eb9a060e54bf8d69288fbee4904

BLENDER ?= blender
PLANS ?= plans.yaml
UPSTREAM := vendor/FloorplanToBlender3d
UT := work/upstream-test
SH3D_SRC_ZIP ?= work/sh3d-src/SweetHome3D-7.6.1-src.zip
SH3D_DTD := work/sh3d/SweetHome3D.dtd
SH3D := uv run python scripts/sh3d.py --dtd $(SH3D_DTD)
SH3D_CMDS := pull push validate inspect preview

ifneq ($(filter $(SH3D_CMDS),$(MAKECMDGOALS)),)
# Command line only: WSL exports NAME=<hostname> into every shell.
ifneq ($(origin NAME),command line)
$(error NAME= required: make <$(SH3D_CMDS)> NAME=<home>)
endif
# NAME reaches the recipe shell before sh3d.py's own check: one word, every char dropped by sh3d_drop.
SH3D_NAME_CHARS := a b c d e f g h i j k l m n o p q r s t u v w x y z A B C D E F G H I J K L M N O P Q R S T U V W X Y Z \
  0 1 2 3 4 5 6 7 8 9 _ -
sh3d_drop = $(if $(1),$(call sh3d_drop,$(wordlist 2,$(words $(1)),$(1)),$(subst $(firstword $(1)),,$(2))),$(2))
ifneq ($(words $(NAME))$(call sh3d_drop,$(SH3D_NAME_CHARS),$(NAME)),1)
$(error NAME='$(NAME)' must match [A-Za-z0-9_-]+)
endif
endif

.PHONY: help sync check whitespace lint fmt test audit push-gate setup-hooks pre-commit upstream-test preprocess convert export models probe sh3d-dtd $(SH3D_CMDS)

help:
	@echo "make sync     - uv sync (locked, all groups) + uv pip check"
	@echo "make check    - verification gate (syncwt runs it): whitespace + lint + test"
	@echo "make lint     - ruff check + ruff format --check + pyright"
	@echo "make fmt      - ruff check --fix + ruff format (rewrites files)"
	@echo "make test     - pytest (skips while tests/ is absent)"
	@echo "make models     - preprocess -> convert -> export, per floor, only steps whose inputs changed"
	@echo "make preprocess - PDFs in plans.yaml -> work/<floor>.png + _grid.png + .json"
	@echo "make convert    - sandboxed upstream run per floor -> work/<floor>/<floor>_raw.blend"
	@echo "make export     - Blender: scale raw blend -> output/<floor>/ .blend .glb .obj previews + wall check"
	@echo "make probe FLOOR=<f> [TARGETS='id=x1,y1:x2,y2 ...'] [BASIS=<ds>] - per-stage wall loss of the last convert -> work/<f>/probe.json"
	@echo "make pull|push|validate|inspect|preview NAME=<home> - SweetHome3D homes/<home>.sh3d <-> sh3d_src/<home>/"
	@echo "make sh3d-dtd  - rebuild work/sh3d/SweetHome3D.dtd from SH3D_SRC_ZIP (validate/push run it)"
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

# -k: a failing floor never stops the others; the summary's exit code reports it.
models:
	@uv run python scripts/pipeline_mk.py --plans $(PLANS) --out work/pipeline.mk
	@$(MAKE) --no-print-directory -k -f work/pipeline.mk floors BLENDER='$(BLENDER)'; \
	  uv run python scripts/pipeline_mk.py --plans $(PLANS) --summary

export:
	uv run python scripts/export.py --blender $(BLENDER)

# Reads the last convert's scratch (work/<f>/ftb) and the newest export report; writes only under work/<f>.
probe:
	@test -n "$(FLOOR)" || { echo "usage: make probe FLOOR=<floor> [TARGETS='id=x1,y1:x2,y2 ...'] [BASIS=<downscale>]" >&2; exit 2; }
	uv run python scripts/sandbox.py --rw work/$(FLOOR) --env MPLBACKEND=Agg --chdir work/$(FLOOR)/ftb -- \
	  $(abspath .venv/bin/python) -I $(abspath scripts/ftb_probe.py) $(abspath work/$(FLOOR)/ftb/floor.ini) \
	  --out $(abspath work/$(FLOOR)/probe.json) \
	  $(foreach r,$(firstword $(wildcard output/$(FLOOR)/$(FLOOR)_report.json output/$(FLOOR)/$(FLOOR)_UNSCALED_report.json)),--report $(abspath $(r))) \
	  $(if $(BASIS),--basis $(BASIS) --meta $(abspath work/$(FLOOR).json)) \
	  $(foreach t,$(TARGETS),--target $(t))

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

sh3d-dtd:
	$(SH3D) dtd --src-zip $(SH3D_SRC_ZIP)

validate push: sh3d-dtd

pull validate inspect preview:
	$(SH3D) $@ $(NAME)

push:
	$(SH3D) push $(NAME)
	@wslpath -w "$$(realpath homes)/$(NAME).sh3d"

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
