# MN Systems Builder

## Home care providers — no-website lead scraper

Collects Minnesota licensed home care providers from the state's public
[Aging and Disability Resources directory](https://mn.gov/adresources/) and
shortlists the ones whose listing shows **no website link**.

Source (server-rendered HTML, pages 1–48):

```
https://mn.gov/adresources/search/?query=LT-2800.3000&query_label=Home+Health+Aide+Services&query_type=taxonomy&page=N
```

`requests` + `BeautifulSoup` only — no Playwright, no Selenium, no search engines.

## How a provider is classified

In each result card's action row, the word **Website** appears either as a real
`<a href="…">` (provider **has** a website) or as bare text with no anchor
(provider has **no** website). The parser checks for the actual anchor element —
never text matching alone — and **never invents or guesses a URL**.

Dead anchors (`href="#"`, `javascript:`, `tel:`, `mailto:`) do not count as a website.

## ⚠️ Accuracy — read this before using the leads

**The state data lags reality.** A provider listed without a website may well
have one. Confirmed example: *Evanpro Home Health Care LLC* shows no website on
the state listing but operates `evanprohomecare.com`.

Every row carries `verified = "unverified"`. This output is a **shortlist of
candidates to verify**, not confirmed fact. Check each one before acting on it.

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Single-page test (recommended first)
python3 scrape_mn_homecare.py --pages 37

# Full run, pages 1–48
python3 scrape_mn_homecare.py

# Parse HTML you saved from the browser (no network needed)
python3 scrape_mn_homecare.py --from-file page37.html --page-label 37
```

Flags: `--pages 1-5,37` · `--out-dir DIR` · `--no-csv` · `--verbose`

## Output

| file | contents |
|---|---|
| `all-providers.csv` | every deduped provider |
| `no-website-leads.csv` | only `has_website=false`, **sorted by city** (work the Twin Cities metro first) |

Columns: `name, license_type, street_address, city, state, zip, phone,
detail_url, has_website, website_url, verified`

Providers are deduped by the detail-page UUID — the same provider can appear on
more than one results page.

## Politeness

1 second between requests, a normal browser User-Agent, and up to 3 retries with
exponential backoff (2s, 4s). A page that still fails is logged and the run
continues — it never crashes, and failed pages are listed in the summary.
