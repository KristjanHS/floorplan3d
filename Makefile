# A stale uv.lock fails every uv command instead of being silently rewritten mid-gate.
export UV_LOCKED := 1
# Empty tree: diff --check against it covers every tracked file, committed or not.
EMPTY_TREE := 4b825dc642cb6eb9a060e54bf8d69288fbee4904

.PHONY: help sync check lint fmt test audit setup-hooks pre-commit

help:
	@echo "make sync     - uv sync (locked, all groups) + uv pip check"
	@echo "make check    - verification gate (syncwt runs it): whitespace + lint + test"
	@echo "make lint     - ruff check + ruff format --check + pyright"
	@echo "make fmt      - ruff check --fix + ruff format (rewrites files)"
	@echo "make test     - pytest (skips while tests/ is absent)"
	@echo "make audit    - uv audit: known vulnerabilities in uv.lock"
	@echo "make setup-hooks - install pre-commit (commit) + make check (push) git hooks"
	@echo "make pre-commit  - run all commit-stage hooks on all files"
	@echo "make syncwt|commit|commitwt - shared worktree targets (~/.config/make/worktree.mk)"

sync:
	uv sync --locked --all-groups
	uv pip check

check:
	@git diff --check $(EMPTY_TREE) -- . ':!*.md'
	@$(MAKE) --no-print-directory lint test

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

fmt:
	uv run ruff check --fix .
	uv run ruff format .

test:
	@if [ -d tests ]; then uv run pytest -q || [ $$? -eq 5 ]; else echo "pytest: no tests/ yet"; fi

audit:
	uv audit

setup-hooks:
	uv run pre-commit install --hook-type pre-commit --hook-type pre-push

pre-commit:
	uv run pre-commit run --all-files

# Bottom, so the include can't steal the default goal; `-include` so clones without dotfiles still work.
-include $(HOME)/.config/make/worktree.mk
