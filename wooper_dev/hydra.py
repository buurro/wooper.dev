from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

import requests
from bs4 import BeautifulSoup, Tag


@dataclass
class Build:
    id: str
    url: str
    date: datetime
    ref: str
    system: str


def _is_succeeded(row: Tag) -> bool:
    """Check if the row represents a succeeded build."""
    status_img = row.find("img", class_="build-status")
    return status_img and "Succeeded" in status_img.get("title", "")


def _parse_build_row(row):
    """Extract relevant data from a succeeded build row."""
    columns = row.find_all("td")
    build_id = columns[1].text.strip()
    build_url = columns[1].find("a")["href"]
    finished_time = columns[2].find("time")["datetime"]
    package = columns[3].text.strip()
    system = columns[4].text.strip()
    return Build(
        id=build_id,
        url=build_url,
        date=datetime.fromisoformat(finished_time),
        ref=package.split(".")[-1],
        system=system,
    )


def _get_next_page_url(soup: BeautifulSoup) -> str | None:
    for link in soup.find_all("a", href=True):
        if link.string == "Next ›":
            href = link.get("href")
            return str(href) if href else None
    return None


def _get_succeeded_builds(soup: BeautifulSoup) -> list[Build]:
    table_rows = soup.select("table.clickable-rows tbody tr")
    succeeded_builds = [
        _parse_build_row(row) for row in table_rows if _is_succeeded(row)
    ]
    return succeeded_builds


def _parse_page(url: str) -> tuple[list[Build], str | None]:
    print(f"Fetching: {url}")
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    builds = _get_succeeded_builds(soup)
    next_page_url = _get_next_page_url(soup)

    return builds, next_page_url


def get_builds(
    before: datetime | None = None,
    after: datetime | None = None,
) -> Iterator[Build]:
    url = "https://hydra.nixos.org/job/nixpkgs/trunk/unstable/all"
    while url:
        builds, next_page = _parse_page(url)
        for build in builds:
            if before and before < build.date:
                continue
            if after and after > build.date:
                return
            yield build
        url = next_page
