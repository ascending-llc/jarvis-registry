"""Generate LLM documentation exports from the MkDocs navigation."""

import argparse
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from markdown_it import MarkdownIt
from mkdocs.config import load_config
from mkdocs.structure.files import get_files
from mkdocs.utils.meta import get_data

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = (
    "Jarvis Registry is a self-hostable enterprise gateway and registry for governed access "
    "to MCP servers, A2A agents, and private AI skills."
)

type Navigation = list[dict[str, str | Navigation]]


def navigation_pages(entries: Navigation, parents: tuple[str, ...] = ()) -> Iterator[tuple[str, str, str]]:
    for entry in entries:
        for title, destination in entry.items():
            if isinstance(destination, list):
                yield from navigation_pages(destination, (*parents, title))
            elif destination.endswith(".md") and not urlsplit(destination).scheme:
                section = parents[0] if parents else "Overview"
                label = ": ".join((*parents[1:], title))
                yield section, label, destination


def page_description(content: str) -> str:
    content, metadata = get_data(content)
    metadata_description = metadata.get("description")
    if isinstance(metadata_description, str) and metadata_description.strip():
        content = metadata_description
    tokens = MarkdownIt().parse(content)
    for position, token in enumerate(tokens[:-1]):
        if token.type != "paragraph_open":
            continue
        paragraph = tokens[position + 1]
        if paragraph.type != "inline":
            continue
        description = " ".join(
            "".join(
                child.content
                if child.type in {"text", "code_inline"}
                else " "
                if child.type in {"softbreak", "hardbreak"}
                else ""
                for child in paragraph.children or []
            ).split()
        )
        if description:
            return description if len(description) <= 240 else description[:237].rsplit(" ", 1)[0] + "..."
    return "Documentation and reference."


def with_update_timestamp(path: Path, content: str) -> str:
    if path.exists():
        previous = path.read_text(encoding="utf-8")
        timestamp = re.search(r"^Last updated: [^\n]+\n\n", previous, re.MULTILINE)
        if timestamp and previous[: timestamp.start()] + previous[timestamp.end() :] == content:
            return previous
    last_updated = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    title, summary, body = content.split("\n\n", 2)
    return f"{title}\n\n{summary}\n\nLast updated: {last_updated}\n\n{body}"


def generate() -> dict[Path, str]:
    config_path = ROOT / "mkdocs.yml"
    config = load_config(config_file=str(config_path))
    site_url = config["site_url"].rstrip("/") + "/"
    docs_dir = Path(config["docs_dir"])
    files = get_files(config)
    pages = list(navigation_pages(config["nav"]))
    index = [
        "# Jarvis Registry",
        "",
        f"> {SUMMARY}",
        "",
        "Use the guides below for deployment, developer clients, business chatbot integrations, "
        "identity, access control, and operations. Capabilities depend on deployment configuration "
        "and the connected platform. Private deployment does not itself confer compliance certification.",
        "",
        f"- [Documentation]({site_url})",
        "- [Source repository](https://github.com/ascending-llc/jarvis-registry)",
        f"- [Full documentation text]({urljoin(site_url, 'llms-full.txt')})",
    ]
    full = [
        "# Jarvis Registry: Full Documentation",
        "",
        f"> {SUMMARY}",
        "",
        f"Documentation: {site_url}",
        f"Index: {urljoin(site_url, 'llms.txt')}",
        "",
        "Generated from local Markdown files in this checkout, using mkdocs.yml navigation order. "
        "Archived and unlisted pages are excluded. Each section includes its published URL and "
        "checkout-relative Markdown source path. Relative links, images, and documentation extension syntax "
        "are preserved from that source; interactive controls require the published site. "
        "The UTC update timestamp records when this export's content last changed. "
        "It is preserved when the content is unchanged, regardless of checkout or file modification times.",
    ]
    seen: set[str] = set()
    current_section = ""
    for section, title, source in pages:
        if source in seen:
            continue
        seen.add(source)
        source_file = files.get_file_from_path(source)
        if source_file is None or not source_file.is_documentation_page():
            raise ValueError(f"Navigation page is missing: {source}")
        content = (docs_dir / source).read_text(encoding="utf-8").strip()
        page_url = urljoin(site_url, source_file.url)
        if section != current_section:
            index.extend(["", f"## {section}", ""])
            current_section = section
        index.append(f"- [{title}]({page_url}): {page_description(content)}")
        full.extend(
            [
                "",
                "---",
                "",
                f"# {section}: {title}",
                "",
                f"Published URL: {page_url}",
                f"Source Markdown: {(docs_dir / source).relative_to(ROOT).as_posix()}",
                "",
                content,
            ]
        )
    exports = {
        docs_dir / "llms.txt": "\n".join(index).rstrip() + "\n",
        docs_dir / "llms-full.txt": "\n".join(full).rstrip() + "\n",
    }
    return {path: with_update_timestamp(path, content) for path, content in exports.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if generated exports are missing or out of date.")
    args = parser.parse_args()
    for path, content in generate().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Out of date: {path.relative_to(ROOT)}; run uv run python scripts/generate_llms.py")
            print(f"Up to date: {path.relative_to(ROOT)}")
        else:
            path.write_text(content, encoding="utf-8")
            print(f"Generated {path.relative_to(ROOT)} ({len(content.encode('utf-8')):,} bytes)")


if __name__ == "__main__":
    main()
