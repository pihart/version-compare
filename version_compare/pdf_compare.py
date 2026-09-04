#!/usr/bin/env python3
"""Create a local visual and text-diff viewer for any two PDF files."""

from __future__ import annotations

import argparse
import difflib
import html
import os
import shutil
import subprocess
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:  # Poppler's pdftotext is a supported dependency-free fallback.
    PdfReader = None  # type: ignore[assignment,misc]

ROOT = Path(os.environ.get("VERSION_COMPARE_PROJECT_ROOT", Path.cwd())).resolve()
DEFAULT_OUTPUT = Path(".build/version-compare-pages")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render two PDFs side by side and generate an HTML text diff."
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--left-label", default="Left document")
    parser.add_argument("--right-label", default="Right document")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dpi", type=int, default=130)
    return parser.parse_args()


def resolve_input(path: Path, project_root: Path) -> Path:
    resolved = path if path.is_absolute() else project_root / path
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"PDF not found: {resolved}")
    return resolved


def render_pdf(pdf: Path, destination: Path, dpi: int) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise RuntimeError("pdftoppm is required; install Poppler and try again")
    subprocess.run(
        [pdftoppm, "-png", "-r", str(dpi), str(pdf), str(destination / "page")],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    pages = sorted(destination.glob("page-*.png"), key=lambda path: int(path.stem.split("-")[-1]))
    if not pages:
        raise RuntimeError(f"No pages were rendered from {pdf}")
    return pages


def normalized_lines(pdf: Path) -> list[str]:
    if PdfReader is not None:
        lines: list[str] = []
        for page_number, page in enumerate(PdfReader(pdf).pages, start=1):
            lines.append(f"PAGE {page_number}")
            for raw_line in (page.extract_text() or "").splitlines():
                line = " ".join(raw_line.replace("\uf0b7", "").replace("•", "").split())
                if line:
                    lines.append(line)
        return lines
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        raise RuntimeError("pdftotext is required; install Poppler and try again")
    result = subprocess.run(
        [pdftotext, "-layout", str(pdf), "-"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    lines: list[str] = []
    for page_number, page in enumerate(result.stdout.split("\f"), start=1):
        if not page.strip():
            continue
        lines.append(f"PAGE {page_number}")
        for raw_line in page.splitlines():
            line = " ".join(raw_line.replace("\uf0b7", "").replace("•", "").split())
            if line:
                lines.append(line)
    return lines


def page_panel(page: Path | None, label: str, number: int, output_dir: Path) -> str:
    if page is None:
        return (
            '<div class="missing" role="img" '
            f'aria-label="{html.escape(label)} has no page {number}">'
            f"No page {number}</div>"
        )
    relative = page.relative_to(output_dir)
    return (
        f'<a href="{html.escape(relative.as_posix())}" target="_blank" title="Open page image">'
        f'<img src="{html.escape(relative.as_posix())}" '
        f'alt="{html.escape(label)}, page {number}" loading="lazy"></a>'
    )


def build_html(
    left_pdf: Path,
    right_pdf: Path,
    left_pages: list[Path],
    right_pages: list[Path],
    left_label: str,
    right_label: str,
    output_dir: Path,
) -> str:
    page_count = max(len(left_pages), len(right_pages))
    page_links = "".join(f'<a href="#page-{number}">{number}</a>' for number in range(1, page_count + 1))
    comparisons: list[str] = []
    for number in range(1, page_count + 1):
        left_page = left_pages[number - 1] if number <= len(left_pages) else None
        right_page = right_pages[number - 1] if number <= len(right_pages) else None
        comparisons.append(
            f'''<section class="page-pair" id="page-{number}">
  <h2>Page {number}</h2>
  <div class="pages">
    <article data-label="{html.escape(left_label, quote=True)}">{page_panel(left_page, left_label, number, output_dir)}</article>
    <article data-label="{html.escape(right_label, quote=True)}">{page_panel(right_page, right_label, number, output_dir)}</article>
  </div>
</section>'''
        )

    text_diff = difflib.HtmlDiff(tabsize=2, wrapcolumn=88).make_table(
        normalized_lines(left_pdf),
        normalized_lines(right_pdf),
        fromdesc=html.escape(left_label),
        todesc=html.escape(right_label),
        context=True,
        numlines=2,
    )
    # HtmlDiff's built-in table is useful but has no semantic class hook.
    text_diff = text_diff.replace('<table class="diff"', '<table class="diff" aria-label="Text differences"')

    title = f"Document comparison: {left_label} vs. {right_label}"
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: light; --ink: #263238; --muted: #66757d; --accent: #167789; --paper: #fff; --line: #d9e1e5; }}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{ margin: 0; background: #eef2f4; color: var(--ink); font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
header {{ position: sticky; top: 0; z-index: 10; padding: 14px 24px; background: rgba(255,255,255,.96); border-bottom: 1px solid var(--line); backdrop-filter: blur(8px); }}
.header-row, main {{ width: min(1800px, calc(100% - 32px)); margin: auto; }}
.header-row {{ display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 20px; }}
h1 {{ margin: 0 0 3px; font-size: 18px; }}
.subtitle {{ color: var(--muted); font-size: 13px; }}
nav {{ display: flex; align-items: center; gap: 7px; white-space: nowrap; }}
nav span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
nav a {{ min-width: 30px; padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px; color: var(--accent); text-align: center; text-decoration: none; }}
main {{ padding: 22px 0 48px; }}
.column-labels, .pages {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 18px; }}
.column-labels {{ position: sticky; top: 78px; z-index: 8; margin-bottom: 12px; }}
.column-labels div {{ padding: 9px 14px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255,255,255,.96); color: var(--accent); font-weight: 700; text-align: center; box-shadow: 0 2px 8px rgba(28,45,52,.06); }}
.page-pair {{ scroll-margin-top: 135px; margin-bottom: 24px; }}
.page-pair h2 {{ margin: 0 0 8px; color: var(--muted); font-size: 13px; font-weight: 650; letter-spacing: .06em; text-transform: uppercase; }}
.pages article {{ min-width: 0; }}
.pages img {{ display: block; width: 100%; height: auto; aspect-ratio: 8.5 / 11; object-fit: contain; border: 1px solid #cfd8dc; background: var(--paper); box-shadow: 0 5px 18px rgba(28,45,52,.14); }}
.missing {{ display: grid; min-height: 420px; place-items: center; border: 2px dashed #bdc8cd; color: var(--muted); background: rgba(255,255,255,.55); }}
details {{ margin-top: 36px; border: 1px solid var(--line); border-radius: 10px; background: var(--paper); overflow: hidden; }}
summary {{ cursor: pointer; padding: 14px 18px; color: var(--accent); font-weight: 700; }}
.diff-wrap {{ overflow-x: auto; padding: 0 16px 18px; }}
table.diff {{ width: 100%; border-collapse: collapse; font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; table-layout: fixed; }}
.diff th {{ padding: 7px; background: #e8eff2; }}
.diff td {{ padding: 2px 5px; overflow-wrap: anywhere; vertical-align: top; }}
.diff_header {{ width: 26px; color: var(--muted); background: #f3f6f7; text-align: right; }}
.diff_next {{ width: 18px; }}
.diff_add {{ background: #d9f5e3; }} .diff_sub {{ background: #ffe0df; }} .diff_chg {{ background: #fff0b3; }}
footer {{ margin-top: 18px; color: var(--muted); font-size: 12px; text-align: center; }}
@media (max-width: 850px) {{
  header {{ position: static; }} .header-row {{ grid-template-columns: 1fr; }} .column-labels {{ position: static; }}
  .column-labels, .pages {{ grid-template-columns: 1fr; }} .column-labels {{ display: none; }}
  .pages article::before {{ content: attr(data-label); display: block; padding: 7px 10px; background: #fff; color: var(--accent); font-weight: 700; }}
}}
</style>
</head>
<body>
<header><div class="header-row">
  <div><h1>{html.escape(title)}</h1><div class="subtitle">Click any page to open its full-resolution image.</div></div>
  <nav aria-label="Page navigation"><span>Pages</span>{page_links}</nav>
</div></header>
<main>
  <div class="column-labels"><div>{html.escape(left_label)}</div><div>{html.escape(right_label)}</div></div>
  {''.join(comparisons)}
  <details><summary>Show wording differences</summary><div class="diff-wrap">{text_diff}</div></details>
  <footer>Generated locally from {html.escape(left_pdf.name)} and {html.escape(right_pdf.name)}.</footer>
</main>
</body>
</html>
'''


def main() -> None:
    args = parse_args()
    project_root = args.project_root.resolve()
    if args.dpi < 72 or args.dpi > 300:
        raise ValueError("--dpi must be between 72 and 300")
    left_pdf = resolve_input(args.left, project_root)
    right_pdf = resolve_input(args.right, project_root)
    output_dir = (args.output_dir if args.output_dir.is_absolute() else project_root / args.output_dir).resolve()
    build_root = (project_root / ".build").resolve()
    if output_dir == build_root or build_root not in output_dir.parents:
        raise ValueError(f"--output-dir must be a child of {build_root}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    left_pages = render_pdf(left_pdf, output_dir / "left", args.dpi)
    right_pages = render_pdf(right_pdf, output_dir / "right", args.dpi)
    rendered = build_html(
        left_pdf,
        right_pdf,
        left_pages,
        right_pages,
        args.left_label,
        args.right_label,
        output_dir,
    )
    index = output_dir / "index.html"
    index.write_text(rendered, encoding="utf-8")
    print(index)


if __name__ == "__main__":
    main()
