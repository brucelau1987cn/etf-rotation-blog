#!/usr/bin/env python3
"""Audit the generated Astro site for structural, metadata, and link regressions."""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
MAX_DESCRIPTION = 180
MAX_TITLE = 80


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.h1_count = 0
        self.ids: list[str] = []
        self.urls: list[str] = []
        self.redirect = False
        self.images: list[dict[str, str | None]] = []
        self.blank_links: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        props = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            if props.get("name") == "description":
                self.description = props.get("content") or ""
            if (props.get("http-equiv") or "").lower() == "refresh":
                self.redirect = True
        elif tag == "link":
            rel = (props.get("rel") or "").split()
            if "canonical" in rel:
                self.canonical = props.get("href") or ""
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "img":
            self.images.append(props)

        element_id = props.get("id")
        if element_id:
            self.ids.append(element_id)

        url = props.get("href") if tag in {"a", "link"} else props.get("src") if tag in {"img", "script"} else None
        if url:
            self.urls.append(url)
        if tag == "a" and props.get("target") == "_blank":
            self.blank_links.append(props)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data


def target_exists(url: str) -> bool:
    path = urlsplit(url).path
    if not path.startswith("/"):
        return True
    target = DIST / path.lstrip("/")
    candidates = [target, target / "index.html", Path(f"{target}.html")]
    return any(candidate.exists() for candidate in candidates)


def main() -> int:
    if not DIST.is_dir():
        print("ERROR: dist/ is missing; run npm run build first")
        return 1

    errors: list[str] = []
    html_files = sorted(DIST.rglob("*.html"))
    normal_pages = 0
    redirect_pages = 0

    for path in html_files:
        rel = path.relative_to(DIST)
        parser = PageParser()
        parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
        if parser.redirect:
            redirect_pages += 1
            continue
        normal_pages += 1

        title = parser.title.strip()
        description = parser.description.strip()
        if not title:
            errors.append(f"{rel}: missing <title>")
        elif len(title) > MAX_TITLE:
            errors.append(f"{rel}: title is {len(title)} chars (max {MAX_TITLE})")
        if not description:
            errors.append(f"{rel}: missing meta description")
        elif len(description) > MAX_DESCRIPTION:
            errors.append(f"{rel}: description is {len(description)} chars (max {MAX_DESCRIPTION})")
        if not parser.canonical:
            errors.append(f"{rel}: missing canonical URL")
        if parser.h1_count != 1:
            errors.append(f"{rel}: expected 1 H1, found {parser.h1_count}")

        duplicates = [item for item, count in Counter(parser.ids).items() if count > 1]
        if duplicates:
            errors.append(f"{rel}: duplicate IDs {duplicates}")

        for image in parser.images:
            if "alt" not in image:
                errors.append(f"{rel}: image missing alt")
            if "width" not in image or "height" not in image:
                errors.append(f"{rel}: image missing width/height")
        for link in parser.blank_links:
            rel_tokens = set((link.get("rel") or "").split())
            if "noopener" not in rel_tokens:
                errors.append(f"{rel}: target=_blank link missing noopener ({link.get('href')})")
        for url in parser.urls:
            if url.startswith("/") and not target_exists(url):
                errors.append(f"{rel}: missing internal target {url}")

    if errors:
        print(f"Static audit failed with {len(errors)} issue(s):")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Static audit passed: {normal_pages} content pages, {redirect_pages} redirects, 0 structural/link errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
