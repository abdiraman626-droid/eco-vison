#!/usr/bin/env python3
"""
Scrape Minnesota licensed home care providers from the state's public
Aging and Disability Resources (ADRS) directory, and shortlist the ones
whose listing has NO website link.

Source (server-rendered HTML, pages 1..48):
  https://mn.gov/adresources/search/?query=LT-2800.3000
      &query_label=Home+Health+Aide+Services&query_type=taxonomy&page=N

Classification rule
-------------------
In each result card's action row the word "Website" appears either as a real
<a href="..."> (provider HAS a website) or as bare text with no anchor
(provider has NO website). We check for the actual anchor element -- never
text matching alone -- and we never invent or guess a URL.

Accuracy
--------
The state data lags reality: a provider listed without a website may well
have one. Every row is therefore stamped verified="unverified". This output
is a shortlist of CANDIDATES TO VERIFY, not confirmed fact.

Usage
-----
  python3 scrape_mn_homecare.py --pages 37          # single-page test
  python3 scrape_mn_homecare.py                     # full run, pages 1-48
  python3 scrape_mn_homecare.py --from-file p37.html --page-label 37
                                                    # parse saved HTML offline
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import time
from collections import Counter
from typing import Iterable

import requests
from bs4 import BeautifulSoup, Tag

BASE_URL = (
    "https://mn.gov/adresources/search/"
    "?query={taxonomy}&query_label={label}"
    "&query_type=taxonomy&page={page}"
)

# Vertical #1, the one whose parse is under test. Others use the same markup,
# so a new taxonomy code is a new lead list with no new parsing code. Find codes
# in the directory's own category links -- never guess one.
DEFAULT_TAXONOMY = "LT-2800.3000"
DEFAULT_LABEL = "Home Health Aide Services"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

FIRST_PAGE = 1
LAST_PAGE = 48
SLEEP_SECONDS = 1.0
MAX_ATTEMPTS = 3
TIMEOUT = 30

ALL_CSV = "{slug}-all.csv"
LEADS_CSV = "{slug}-no-website-leads.csv"

FIELDNAMES = [
    "name",
    "license_type",
    "street_address",
    "city",
    "state",
    "zip",
    "phone",
    "detail_url",
    "has_website",
    "website_url",
    "verified",
]

# /adresources/search/<uuid>/ -- the provider detail page.
DETAIL_HREF_RE = re.compile(
    r"/adresources/search/"
    r"(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?",
)

PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")
# "Minneapolis, MN 55401" / "St. Paul, MN 55101-1234"
CITY_STATE_ZIP_RE = re.compile(
    r"(?P<city>[A-Za-z][A-Za-z.'\-\s]*?)\s*,\s*"
    r"(?P<state>[A-Z]{2})\s+"
    r"(?P<zip>\d{5}(?:-\d{4})?)\b"
)
LICENSE_RE = re.compile(r"\b[A-Za-z][A-Za-z\s/&\-]*?Licens[e|ed]\w*\b")

log = logging.getLogger("mn-homecare")


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def fetch_page(session: requests.Session, url: str, page: int) -> str | None:
    """GET one results page. Retry up to MAX_ATTEMPTS with backoff.

    Returns the HTML, or None if every attempt failed (logged, never raised).
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            if attempt == MAX_ATTEMPTS:
                log.error("page %s FAILED after %s attempts: %s", page, attempt, exc)
                return None
            backoff = 2 ** attempt  # 2s, 4s
            log.warning(
                "page %s attempt %s/%s failed (%s); retrying in %ss",
                page, attempt, MAX_ATTEMPTS, exc, backoff,
            )
            time.sleep(backoff)
    return None


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slugify(text: str) -> str:
    """'Home Health Aide Services' -> 'home-health-aide-services'."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def _detail_anchors(soup: BeautifulSoup) -> list[Tag]:
    """Every anchor pointing at a provider detail page."""
    anchors: list[Tag] = []
    for anchor in soup.find_all("a", href=True):
        if DETAIL_HREF_RE.search(anchor["href"]):
            anchors.append(anchor)
    return anchors


def _find_card(anchor: Tag) -> Tag:
    """Climb from the provider-name link to the enclosing result card.

    Structure-agnostic on purpose: rather than depending on the site's class
    names (which change), take the nearest ancestor that still wraps exactly
    one provider link and that carries the action row ("Website"/"Directions"
    or a phone number). That is the card.
    """
    best = anchor
    node = anchor.parent
    while isinstance(node, Tag) and node.name not in ("body", "html", "[document]"):
        links = [a for a in node.find_all("a", href=True)
                 if DETAIL_HREF_RE.search(a["href"])]
        if len(links) > 1:
            break  # we have swallowed a sibling card -- previous level was the card
        text = node.get_text(" ", strip=True)
        if ("Website" in text or "Directions" in text) or PHONE_RE.search(text):
            best = node
        node = node.parent
    return best if best is not anchor else (anchor.parent or anchor)


def _website_from_card(card: Tag, name_anchor: Tag) -> tuple[bool, str]:
    """THE KEY RULE.

    "Website" in the action row is an <a href> when the provider has one, and
    bare text when it does not. So: look for an actual anchor whose visible
    text is "Website". No anchor -> no website. We never synthesise a URL.
    """
    for anchor in card.find_all("a", href=True):
        if anchor is name_anchor:
            continue
        label = _norm(anchor.get_text()).lower()
        if label != "website" and not re.fullmatch(r"website\b.*", label):
            continue
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "tel:", "mailto:")):
            continue  # a dead anchor is not a website
        return True, href

    # Fallback: some markup labels the link only via aria-label/title while the
    # visible text is an icon. Still an anchor, so still a real website.
    for anchor in card.find_all("a", href=True):
        if anchor is name_anchor:
            continue
        meta = " ".join(
            filter(None, [anchor.get("aria-label"), anchor.get("title")])
        ).lower()
        if "website" not in meta:
            continue
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "tel:", "mailto:")):
            continue
        return True, href

    return False, ""


def _lines(card: Tag) -> list[str]:
    out: list[str] = []
    for raw in card.get_text("\n", strip=True).split("\n"):
        line = _norm(raw)
        if line:
            out.append(line)
    return out


def parse_page(html: str, page: int) -> list[dict]:
    """Parse one results page into provider dicts."""
    soup = BeautifulSoup(html, "lxml")
    providers: list[dict] = []

    for anchor in _detail_anchors(soup):
        href = anchor["href"]
        match = DETAIL_HREF_RE.search(href)
        if not match:
            continue
        uuid = match.group("uuid").lower()

        name = _norm(anchor.get_text())
        if not name:
            continue  # an image/anchor wrapper, not the name link

        card = _find_card(anchor)
        lines = _lines(card)
        card_text = " ".join(lines)

        # phone
        phone_match = PHONE_RE.search(card_text)
        phone = _norm(phone_match.group(0)) if phone_match else ""

        # city / state / zip, and the street line that precedes them
        city = state = zip_code = street = ""
        for index, line in enumerate(lines):
            csz = CITY_STATE_ZIP_RE.search(line)
            if not csz:
                continue
            city = _norm(csz.group("city"))
            state = csz.group("state")
            zip_code = csz.group("zip")
            head = _norm(line[: csz.start()]).rstrip(",")
            if head:
                street = head          # "123 Main St, Eagan, MN 55121" on one line
            elif index > 0:
                street = lines[index - 1]   # street on its own line above
            break

        # licence type
        license_type = ""
        for line in lines:
            if "licens" in line.lower():
                hit = LICENSE_RE.search(line)
                license_type = _norm(hit.group(0)) if hit else line
                break

        has_website, website_url = _website_from_card(card, anchor)

        providers.append(
            {
                "uuid": uuid,
                "name": name,
                "license_type": license_type,
                "street_address": street,
                "city": city,
                "state": state,
                "zip": zip_code,
                "phone": phone,
                "detail_url": requests.compat.urljoin("https://mn.gov", href),
                "has_website": has_website,
                "website_url": website_url,
                "verified": "unverified",
                "_page": page,
            }
        )

    return providers


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def write_csv(path: str, rows: Iterable[dict]) -> int:
    count = 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["has_website"] = "true" if row["has_website"] else "false"
            writer.writerow(out)
            count += 1
    return count


def print_summary(providers: list[dict], leads: list[dict], failed: list[int]) -> None:
    print()
    print("=" * 64)
    print("SUMMARY")
    print("=" * 64)
    print(f"Total providers found (deduped) : {len(providers)}")
    print(f"No website on state listing     : {len(leads)}")
    if providers:
        pct = 100.0 * len(leads) / len(providers)
        print(f"Share with no website           : {pct:.1f}%")
    if failed:
        print(f"Pages that FAILED to fetch      : {sorted(failed)}")

    counts = Counter(p["city"] or "(unknown)" for p in leads)
    total_by_city = Counter(p["city"] or "(unknown)" for p in providers)
    print()
    print("Top 15 cities by no-website leads:")
    print(f"  {'City':<24}{'Leads':>7}{'Total':>8}")
    print(f"  {'-' * 24}{'-' * 7}{'-' * 8}")
    for city, n in counts.most_common(15):
        print(f"  {city:<24}{n:>7}{total_by_city[city]:>8}")
    print()
    print('NOTE: every row is verified="unverified". The state directory lags')
    print("reality -- a provider listed without a website may still have one.")
    print("Treat this as a shortlist of candidates to verify, not as fact.")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def dedupe(providers: list[dict]) -> list[dict]:
    """Same provider can appear on more than one page -- key on detail UUID."""
    seen: dict[str, dict] = {}
    for provider in providers:
        existing = seen.get(provider["uuid"])
        if existing is None:
            seen[provider["uuid"]] = provider
        elif not existing["has_website"] and provider["has_website"]:
            seen[provider["uuid"]] = provider  # prefer the richer record
    return list(seen.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pages", default=None,
        help="Pages to fetch, e.g. '37' or '1-5' or '1,3,37'. Default 1-48.",
    )
    parser.add_argument(
        "--from-file", action="append", default=[], metavar="PATH",
        help="Parse saved HTML instead of fetching (repeatable). Offline test mode.",
    )
    parser.add_argument("--page-label", default="?", help="Page number for --from-file logs.")
    parser.add_argument(
        "--taxonomy", default=DEFAULT_TAXONOMY,
        help=f"Directory taxonomy code, e.g. {DEFAULT_TAXONOMY}. One code = one vertical.",
    )
    parser.add_argument(
        "--label", default=DEFAULT_LABEL,
        help="Human label for the taxonomy. Names the output files.",
    )
    parser.add_argument(
        "--last-page", type=int, default=LAST_PAGE,
        help=f"Highest page to try (default {LAST_PAGE}). Page counts differ per vertical; "
             "the run stops early on the first page that returns zero providers.",
    )
    parser.add_argument("--out-dir", default=".", help="Where to write the CSVs.")
    parser.add_argument("--no-csv", action="store_true", help="Print only, write nothing.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    raw: list[dict] = []
    failed: list[int] = []

    if args.from_file:
        for path in args.from_file:
            with open(path, encoding="utf-8", errors="replace") as handle:
                html = handle.read()
            found = parse_page(html, args.page_label)
            log.info("%s -> %s providers", path, len(found))
            raw.extend(found)
    else:
        pages = parse_page_spec(args.pages, args.last_page)
        label = requests.compat.quote_plus(args.label)
        session = make_session()
        log.info("vertical: %s (%s)", args.label, args.taxonomy)
        for index, page in enumerate(pages):
            url = BASE_URL.format(taxonomy=args.taxonomy, label=label, page=page)
            log.info("fetching page %s/%s ...", page, pages[-1])
            html = fetch_page(session, url, page)
            if html is None:
                failed.append(page)
            else:
                found = parse_page(html, page)
                log.info("page %s -> %s providers", page, len(found))
                raw.extend(found)
                # Page counts differ per vertical, so an empty page is the end
                # of results -- not an error, as long as the fetch succeeded.
                if not found and len(pages) > 1:
                    log.info("page %s returned no providers -- stopping here", page)
                    break
            if index < len(pages) - 1:
                time.sleep(SLEEP_SECONDS)  # be polite

    providers = dedupe(raw)
    providers.sort(key=lambda p: (p["city"].lower(), p["name"].lower()))
    leads = [p for p in providers if not p["has_website"]]
    leads.sort(key=lambda p: (p["city"].lower(), p["name"].lower()))  # Twin Cities first

    duplicates = len(raw) - len(providers)
    if duplicates:
        log.info("removed %s duplicate row(s) by detail UUID", duplicates)

    if not args.no_csv:
        os.makedirs(args.out_dir, exist_ok=True)
        slug = slugify(args.label)
        all_path = os.path.join(args.out_dir, ALL_CSV.format(slug=slug))
        leads_path = os.path.join(args.out_dir, LEADS_CSV.format(slug=slug))
        log.info("wrote %s rows -> %s", write_csv(all_path, providers), all_path)
        log.info("wrote %s rows -> %s", write_csv(leads_path, leads), leads_path)

    print_summary(providers, leads, failed)
    return 0


def parse_page_spec(spec: str | None, last_page: int = LAST_PAGE) -> list[int]:
    if not spec:
        return list(range(FIRST_PAGE, last_page + 1))
    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            start, end = chunk.split("-", 1)
            pages.extend(range(int(start), int(end) + 1))
        elif chunk:
            pages.append(int(chunk))
    return sorted(set(pages))


if __name__ == "__main__":
    sys.exit(main())
