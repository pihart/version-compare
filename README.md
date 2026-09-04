# Version Compare

A private, reusable toolkit for comparing structured document versions. It
provides:

- section-aware and word-aware content diffs;
- normalized visual comparisons and exact rendered-PDF page comparisons;
- a preference graph spanning revisions and named profiles;
- strict-preference and intentional-incomparability decisions with reasons;
- a prioritized queue for identifying maximal candidate versions;
- a standalone static side-by-side PDF viewer.

The toolkit contains no document content or project-specific parsing rules.
Host repositories supply those through a small Python adapter.

## Adapter interface

An adapter file exports `create_adapter(project_root)`. The returned object must
provide:

```python
preferences_path: pathlib.Path
generated_root: pathlib.Path  # optional

def list_revisions() -> list[dict]: ...
def available_profiles(revision: str) -> list[dict]: ...
def load_version(revision: str, profile: str) -> dict: ...
def visual_path(revision: str, profile: str) -> tuple[pathlib.Path | None, str]: ...  # optional
def refresh() -> None: ...  # optional
```

Revision, profile, and loaded-version records may set `recordable: false` for
working data or external reference material. A loaded version supplies a stable
content hash and an ordered `blocks` array. Each block has an `id`, `text`,
`kind`, and `section`; an optional `match_key` can align semantically equivalent
blocks from heterogeneous sources.

## Run the interactive tool

```sh
python3 -m pip install -e .
version-compare \
  --project-root /path/to/project \
  --adapter path/to/project_adapter.py \
  --open
```

The server binds only to localhost. Exact visual rendering requires Poppler's
`pdftoppm` executable.

## Compare two PDFs directly

```sh
pdf-version-compare \
  --project-root /path/to/project \
  --left output/edition-a.pdf \
  --right output/edition-b.pdf \
  --left-label "Edition A" \
  --right-label "Edition B"
```

The static PDF viewer requires Poppler and uses `pypdf` for text extraction.

## Development

```sh
make check
```

The test suite uses an in-memory adapter and contains no host-project data.
