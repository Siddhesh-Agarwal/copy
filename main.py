import os
import re
from pathlib import Path
from queue import Queue
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import typer
from bs4 import BeautifulSoup, Tag
from rich.console import Console

app = typer.Typer(
    name="copy",
    no_args_is_help=True,
    help="Website crawler that downloads all resources recursively",
)
console = Console()


def is_valid(url: str):
    parsed = urlparse(url)
    return parsed.scheme in ["http", "https"]


def should_download(url: str, base_domain: str):
    return urlparse(url).netloc == base_domain


def save_resource(response: requests.Response, output_dir: str, domain: str, url: str):
    parsed = urlparse(url)
    path = parsed.path

    if not path or path.endswith("/"):
        path += "index.html"

    path_parts = path.strip("/").split("/")
    dir_structure = os.path.join(output_dir, domain, *path_parts[:-1])
    filename = path_parts[-1] if path_parts else "index.html"

    os.makedirs(dir_structure, exist_ok=True)
    file_path = os.path.join(dir_structure, filename)

    with open(file_path, "wb") as f:
        f.write(response.content)
    return file_path


def extract_html_links(content, base_url):
    soup = BeautifulSoup(content, "html.parser")
    links = []

    tags = {
        "a": "href",
        "link": "href",
        "img": ["src", "srcset"],
        "script": "src",
        "source": ["src", "srcset"],
    }

    for tag, attrs in tags.items():
        for element in soup.find_all(tag):
            if isinstance(attrs, list):
                for attr in attrs:
                    if isinstance(element, Tag) and (value := element.get(attr)):
                        if isinstance(value, str):
                            links.extend([v.split(maxsplit=1)[0] for v in value.split(",")])
            elif isinstance(element, Tag) and (value := element.get(attrs)):
                links.append(value)

    return [urljoin(base_url, link) for link in links]


def extract_css_links(content, base_url):
    urls = re.findall(r'url\(\s*[\'"]?(.*?)[\'"]?\s*\)', content, re.IGNORECASE)
    return [urljoin(base_url, url) for url in urls]


@app.command()
def crawl(
    url: str = typer.Argument(..., help="Starting URL to crawl"),
    output_dir: Path = typer.Option(
        Path("./downloaded"),
        "--output",
        "-o",
        help="Output directory for downloaded files",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    max_depth: Optional[int] = typer.Option(
        None, "--max-depth", "-d", help="Maximum recursion depth (default: unlimited)",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show detailed progress information",
    ),
):
    """Recursively download all resources from a website
    """
    try:
        # Create output directory if needed
        output_dir.mkdir(parents=True, exist_ok=True)
        if max_depth is not None and max_depth < 0:
            max_depth = None

        if verbose:
            console.print(f"Starting crawl of {url}")
            console.print(f"Saving files to: {output_dir.resolve()}")

        # Resolve initial redirects
        response = requests.get(
            url, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True,
        )
        if response.status_code != 200:
            console.print(f"[red bold]Error: Initial URL returned {response.status_code}[/]")
            raise typer.Exit(code=1)

        base_url = response.url
        base_domain = urlparse(base_url).netloc
        visited = set()
        queue = Queue()
        queue.put((base_url, 0))

        while not queue.empty():
            url, depth = queue.get()

            if max_depth is not None and depth > max_depth:
                if verbose:
                    console.print(f"Skipping {url} (max depth reached)")
                continue

            if url in visited:
                continue
            visited.add(url)

            if verbose:
                console.print(f"Downloading: {url}")

            try:
                response = requests.get(
                    url, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True,
                )
                response.raise_for_status()
            except Exception as e:
                console.print(f"[red]Error downloading {url}: {e}[/]")
                continue

            final_url = response.url
            if not should_download(final_url, base_domain):
                if verbose:
                    console.print("[red]Skipping {url} (not a supported resource type)…[/]")
                continue

            saved_path = save_resource(
                response, str(output_dir), base_domain, final_url,
            )
            if verbose:
                console.print(f"[green]Saved: {saved_path}[/]")

            content_type = (
                response.headers.get("Content-Type", "").split(";")[0].strip()
            )
            links = []

            if content_type == "text/html":
                try:
                    links = extract_html_links(response.content, final_url)
                except Exception as e:
                    console.print(f"[red]HTML parsing error: {e}[/]")
            elif content_type == "text/css":
                try:
                    links = extract_css_links(response.text, final_url)
                except Exception as e:
                    console.print(f"[red]CSS parsing error: {e}[/]")

            for link in links:
                parsed = urlparse(link)
                clean_link = parsed._replace(query="", fragment="")
                clean_url = urlunparse(clean_link)

                if is_valid(clean_url) and should_download(clean_url, base_domain):
                    if clean_url not in visited:
                        queue.put((clean_url, depth + 1))

    except Exception as e:
        console.print(f"[red bold]Critical error: {e}[/]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
