# AGENTS.md

This is the one instruction file. A `CLAUDE.md` symlink pointing here is a convenience some checkouts set up locally; it is not tracked, so a fresh clone has this file only.

## Project Overview

pagewielder is a Python CLI tool for manipulating PDFs: filtering pages by dimensions and extracting page ranges. Built with pikepdf.

## Development Commands

All development tasks are managed through `run.py`, which supports both system Python and virtual environment execution.

### Environment Setup
```bash
./run.py create-env          # Create virtual environment in ./env with all dev dependencies
```

### Code Quality
```bash
./run.py check               # Run mypy type checks (strict mode)
./run.py lint                # Run flake8 and pylint
./run.py fmt                 # Format with isort and black
./run.py test                # Run unittest tests
```

`check` and `fmt` cover `pagewielder/`, `tests/` and `version.py`.  `test` discovers from `tests/` alone.
`lint` runs pylint over the same three, but invokes flake8 with no path, so flake8 walks the repo root minus
the `.flake8` excludes -- `run.py` included.

Add `-e` or `--venv` flag to use the virtual environment (e.g., `./run.py -e check`).

### Version Generation

The version is computed at build time.  Hatchling runs `version.py` as its version source, which reads the base
version from the `VERSION` file and appends the git reference: `PAGEWIELDER_GIT_REF` if set, otherwise
`git rev-parse --short HEAD`, otherwise nothing.  The result looks like `0.1.0+ac0c2e6`.  `flake.nix` reads
`VERSION` too.

The result goes into the package metadata, and `pagewielder/__init__.py` reads `__version__` from there with
`importlib.metadata`, falling back to `unknown` when the package isn't installed.  An editable install records the
version when it is made, so in a checkout it reports the hash from the last `create-env` rather than the current HEAD.

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
  - Private helpers cover the bookkeeping: `_page_labels()` / `_set_page_labels()` for `/PageLabels`, with `_continues()` deciding where one range carries on into the next; `_prune_outline_items()` and `_outline_item_page()` for the outline; `_prune_destinations()`, `_destination_page()`, `_resolve_named_destination()` and `_dests_name_tree()` for destinations.
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
- **`/PageLabels`**: each surviving page keeps its label, and the ranges are rebuilt against the new indices, merging ranges that run on.

Known limits, deliberate: link annotations on the remaining pages are not touched, so a file using them keeps dangling links and the pages they name. Malformed or unreadable `/PageLabels` are left alone rather than treated as an error.

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
- Dev dependencies (`.[dev]`): black, flake8, isort, mypy, pylint; the `test` and `types` extras exist but are empty
- Console script: `pagewielder = "pagewielder.__main__:main"`
- The package ships `py.typed`

Tool settings live in `pyproject.toml` and `.flake8`; see Code Style below for what they amount to.

### Nix Build
```bash
nix build                    # Build with Nix flakes
```

The Nix build sets `PAGEWIELDER_GIT_REF` to the flake's revision, since the sandbox has no `.git`, and runs `./run.py check` as its check phase.

## Testing

Tests use Python's unittest framework, discovered from `tests/`.

- `tests/test_core.py`: `map_dimensions_to_pages` plus the bulk of the suite on `remove_pages` — outline pruning, named destinations, `/GoTo` actions, `/PageLabels` remapping, and the malformed-input cases.
- `tests/test_cli.py`: `parse_page_range` and an end-to-end `excerpt` run.
- `tests/helpers.py`: builders and readers shared by both — `make_pdf()`, `outline_titles()`, `set_page_labels()`, `page_label_ranges()`, and the `A4` / `PLATE` page sizes. Prefer extending these over hand-rolling PDF fixtures.

Run specific test:
```bash
python -m unittest tests.test_core.RemovePagesTest.test_removes_pages
```

## CI/CD

GitHub Actions workflow (`.github/workflows/ci.yml`) runs on push/PR to master (and `workflow_dispatch`), on Python 3.11:
1. `./run.py -e create-env`
2. `./run.py -e check`
3. `./run.py -e lint`

There is no test step in CI; run `./run.py test` locally.

## Code Style

- Max line length: 120 characters, set for black, isort, pylint and flake8 alike
- Black formatting with isort for imports
- Type hints required: mypy strict over `pagewielder`, `tests` and `version.py`, pyright strict over the same
- flake8 ignores E203 (whitespace before ':') and F401 in `__init__.py`; pylint disables C0301 (line-too-long, black's job) and C0414 (useless-import-alias)
- Google-style docstrings with Args/Returns/Raises on public and private functions alike
- Comments explain why a case is handled, not what the line does; the existing code is the reference for tone
