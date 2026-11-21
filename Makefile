BLENDER ?= blender
PY_SRC := $(shell git ls-files '*.py' ':!vendor/**')

.PHONY: help check

help:
	@echo "make check    - verification gate (syncwt runs it): whitespace, py_compile, pytest if tests/ exists"
	@echo "make syncwt|commit|commitwt - shared worktree targets (~/.config/make/worktree.mk)"

check:
	@git diff --check HEAD
	@$(if $(PY_SRC),python3 -m py_compile $(PY_SRC) && echo "py_compile ok: $(words $(PY_SRC)) files",echo "py_compile: no .py yet")
	@if [ -d tests ]; then uv run pytest -q; else echo "pytest: no tests/ yet"; fi

# Bottom, so the include can't steal the default goal; `-include` so clones without dotfiles still work.
-include $(HOME)/.config/make/worktree.mk
