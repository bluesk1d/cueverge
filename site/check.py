"""Check the built site's local links, fragments, and image descriptions."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.errors: list[str] = []
        self.feed(path.read_text())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if identity := values.get("id"):
            if identity in self.ids:
                self.errors.append(f"Duplicate id: {identity}")
            self.ids.add(identity)
        if tag in {"a", "img", "link"} and (url := values.get("href") or values.get("src")):
            self.links.append(url)
        if tag == "img" and not values.get("alt"):
            self.errors.append("Image is missing descriptive alt text")


def main() -> None:
    root = Path(__file__).parent.joinpath("dist").resolve()
    pages = {path: Page(path) for path in root.rglob("*.html")}
    errors: list[str] = []
    if not pages:
        raise SystemExit("No built pages found. Run npm run build first.")
    for path, page in pages.items():
        for error in page.errors:
            errors.append(f"{path.relative_to(root)}: {error}")
        for url in page.links:
            parts = urlsplit(url)
            if parts.scheme or parts.netloc:
                continue
            target = (path.parent / unquote(parts.path)).resolve() if parts.path else path
            if (target != root and root not in target.parents) or not target.exists():
                errors.append(f"{path.relative_to(root)}: missing local target {url}")
            elif (
                parts.fragment
                and target in pages
                and unquote(parts.fragment) not in pages[target].ids
            ):
                errors.append(f"{path.relative_to(root)}: missing fragment {url}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Checked {len(pages)} pages: local links, fragments, unique IDs, and image alt text.")


if __name__ == "__main__":
    main()
