BLENDER ?= blender

.PHONY: help sync check lint fmt test audit

help:
	@echo "make sync     - uv sync (locked, all groups) + uv pip check"
	@echo "make check    - verification gate (syncwt runs it): whitespace + lint + test"
	@echo "make lint     - ruff check + ruff format --check + pyright"
	@echo "make fmt      - ruff check --fix + ruff format (rewrites files)"
	@echo "make test     - pytest (skips while tests/ is absent)"
	@echo "make audit    - uv audit: known vulnerabilities in uv.lock"
	@echo "make syncwt|commit|commitwt - shared worktree targets (~/.config/make/worktree.mk)"

sync:
	uv sync --locked --all-groups
	uv pip check

check:
	@git diff --check HEAD
	@$(MAKE) --no-print-directory lint test

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

fmt:
	uv run ruff check --fix .
	uv run ruff format .

test:
	@if [ -d tests ]; then uv run pytest -q; else echo "pytest: no tests/ yet"; fi

audit:
	uv audit

# Bottom, so the include can't steal the default goal; `-include` so clones without dotfiles still work.
-include $(HOME)/.config/make/worktree.mk
