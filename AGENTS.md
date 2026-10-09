# AGENTS.md

This is the one instruction file. A `CLAUDE.md` symlink pointing here is a convenience some checkouts set up locally; it is not tracked, so a fresh clone has this file only.

## Project Overview

pagewielder is a Python CLI tool for manipulating PDFs: filtering pages by dimensions and extracting page ranges. Built with pikepdf.

## Development Commands

There is no task runner.  The formatters, linters and type checkers run as [pre-commit](https://pre-commit.com)
hooks, declared in `flake.nix` with [git-hooks.nix](https://github.com/cachix/git-hooks.nix).  Entering the dev shell
generates `.pre-commit-config.yaml` (a gitignored symlink into the Nix store; don't edit or commit it) and installs the
git hook.

```bash
nix develop                                # Shell with the tools; installs the hook
pre-commit run --all-files                 # Run the hooks over the whole repo by hand
python -m unittest discover -v -s tests    # Test (not a hook)
nix flake check                            # Build and test the package, and run the hooks, in the sandbox
```

Without Nix, `pip install -e '.[dev]'` installs the Python tools from PyPI, to run by hand; there is no hook
configuration outside the dev shell.

The hooks are nixfmt, ruff (lint, with `--fix`) and ruff-format on the changed files, and mypy and pyright on the
whole project, which both take what to check from `pyproject.toml`.  mypy and pyright run against `typingEnv`, a
Python with pikepdf, since the hooks' own tools see no third-party packages otherwise.

### Version Generation

The version is computed at build time.  Hatchling runs `version.py` as its version source, which reads the base
version from the `VERSION` file and appends the git reference: `PAGEWIELDER_GIT_REF` if set, otherwise
`git rev-parse --short HEAD`, otherwise nothing.  The result looks like `0.1.0+ac0c2e6`.  `flake.nix` reads
`VERSION` too.

The result goes into the package metadata, and `pagewielder/__init__.py` reads `__version__` from there with
`importlib.metadata`, falling back to `unknown` when the package isn't installed.  An editable install records the
version when it is made, so in a checkout it reports the hash from the last install rather than the current HEAD.  In
the dev shell the package isn't installed at all, so it reports `unknown`.

### Running the Application
```bash
python -m pagewielder filter <input.pdf> [-o <output.pdf>]           # Filter pages by dimensions
python -m pagewielder excerpt <input.pdf> <pages> [-o <output.pdf>]  # Extract page range
python -m pagewielder --version
```

Page ranges use 1-based indexing: `1:5` (pages 1-5), `3:` (page 3 to end), `:10` (start to page 10), `7` (page 7 only).

With no `-o`, both commands write to a fresh temporary file and print its path. Writing back over the input is refused.

## Architecture

### Module Structure

- **pagewielder/core.py**: Core PDF manipulation logic.
  - `map_dimensions_to_pages()`: Groups page numbers by page dimensions, reading each page's
    size through `_get_dimensions()`, which measures the mediabox with pikepdf's `Rectangle`.
  - `remove_pages()`: Removes pages in place, keeping the rest of the document consistent (see below).
  - Private helpers cover the bookkeeping: `_page_labels()` / `_set_page_labels()` for `/PageLabels`, with `_continues()` deciding where one range carries on into the next; `_prune_outline_items()` for the outline; `_prune_links()` for link annotations; `_goto_page()`, shared by the outline and links, then `_prune_destinations()`, `_destination_page()`, `_resolve_named_destination()` and `_dests_name_tree()` for destinations.
  - Uses pikepdf's `Page`, `Rectangle`, `NameTree`, `NumberTree` and outline APIs.

- **pagewielder/cli.py**: Command-line interface.
  - Two commands: `filter` (interactive dimension selection) and `excerpt` (page range extraction).
  - `parse_page_range()`: Handles the page range syntax above.
  - `select_dimensions()`: Interactive prompt for dimension-based filtering.  Its prompt strings are
    module constants (`PROMPT_*`); a test that drives this path should match on those rather than on
    literal text.  Nothing tests it today.
  - `_resolve_output_path()`: Shared by both commands; allocates the temporary file and rejects an output equal to the input.
  - `main()` takes an optional argument sequence and returns an exit code, so it can be driven
    directly.  The current tests call the `*_command` functions with a hand-built `Namespace`
    instead; either entry point works.

- **pagewielder/__main__.py**: Entry point that delegates to `cli.main()`.

### What `remove_pages` preserves

- **Outline**: items pointing at a removed page are dropped and replaced by their children. Destinations are followed through named destinations (`/Root /Dests` and the `/Root /Names /Dests` name tree) and through `/GoTo` actions, in any order, bounded by `_MAX_DESTINATION_HOPS`.
- **Destinations**: stale entries in `/Root /Dests`, the `/Root /Names /Dests` tree, and `/Root /OpenAction` are deleted, which also stops the removed page objects from being written back out.
- **Links**: `/Link` annotations on the remaining pages whose `/Dest` or `/GoTo` action resolves to a removed page are deleted outright, not left as inert clickable regions. Links are pruned before destinations, since resolving one may need a named destination that is about to go.
- **`/PageLabels`**: each surviving page keeps its label, and the ranges are rebuilt against the new indices, merging ranges that run on.

Known limits, deliberate: the structure tree (`/StructTreeRoot`) and article threads (`/Threads`) are not touched, so a file using them keeps the pages they name. In a tagged PDF, the structure tree can also go on referring to a pruned link, which then sits on no page (#30). Malformed or unreadable `/PageLabels` are left alone rather than treated as an error.

### Type Aliases
```python
Dimensions = tuple[float, float]  # (width, height)
Pages = set[int]                  # 1-based page numbers
```

## Build System

Uses hatchling for building. The package can also be built with Nix (see `flake.nix`).

### pyproject.toml Configuration
- Python >=3.11 required
- Version from `version.py` (see Version Generation); description is static
- Single runtime dependency: pikepdf >=7.1.2
- Dev dependencies (`.[dev]`): mypy, pyright, ruff
- Console script: `pagewielder = "pagewielder.__main__:main"`
- The package ships `py.typed`

Tool settings live in `pyproject.toml`; see Code Style below for what they amount to.

### Nix Build
```bash
nix build                    # Build; the check phase runs the tests
nix flake check              # Build, plus checks.pre-commit: the hooks over the whole repo
```

The Nix build sets `PAGEWIELDER_GIT_REF` to the flake's revision, since the sandbox has no `.git`, and runs the tests with `unittestCheckHook` as its check phase.  The hooks run separately, as `checks.pre-commit`; the comment in `flake.nix` says why.

## Testing

Tests use Python's unittest framework, discovered from `tests/`.

- `tests/test_core.py`: `map_dimensions_to_pages` plus the bulk of the suite on `remove_pages` — outline pruning, link annotations, named destinations, `/GoTo` actions, `/PageLabels` remapping, and the malformed-input cases.
- `tests/test_cli.py`: `parse_page_range` and an end-to-end `excerpt` run.
- `tests/helpers.py`: builders and readers shared by both — `make_pdf()`, `outline_titles()`, `link()`, `set_annotations()`, `annotation_ids()`, `count_page_objects()`, `set_page_labels()`, `page_label_ranges()`, and the `A4` / `PLATE` page sizes. Prefer extending these over hand-rolling PDF fixtures.

Run specific test:
```bash
python -m unittest tests.test_core.RemovePagesTest.test_removes_pages
```

## CI/CD

GitHub Actions workflow (`.github/workflows/ci.yml`) runs `nix flake check` on push/PR to master (and `workflow_dispatch`), which builds and tests the package and runs the hooks.  The Python version is whatever the locked nixpkgs provides; `requires-python` says 3.11 or later, but nothing tests 3.11 itself.

## Code Style

- Max line length: 120 characters
- `ruff format` formatting (black's style), with ruff's isort rules for imports
- Type hints required: mypy strict over `pagewielder`, `tests` and `version.py`, pyright strict over the same
- ruff selects pycodestyle (E, W), pyflakes (F), isort (I), pylint (PL) and pydocstyle (D), the last with the Google convention
- Google-style docstrings with Args/Returns/Raises on public and private functions alike
- Comments explain why a case is handled, not what the line does; the existing code is the reference for tone
