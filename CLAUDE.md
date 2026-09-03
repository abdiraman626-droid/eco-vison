# MN Systems Builder — context

## What this is

A lead engine that finds **businesses with NO website**, so Abdul can sell them
one. The output is a prospecting shortlist.

Source #1 is the Minnesota Aging and Disability Resources (ADRS) directory.
Home care is vertical #1, not the whole point -- the category is a flag, and the
same code works for every other category in that directory.

Owner: Abdul (abdirahman). Goal is a workable call list, metro first.

## Source

Server-rendered HTML, pages 1-48:

```
https://mn.gov/adresources/search/?query=<taxonomy>&query_label=<label>&query_type=taxonomy&page=N
```

Vertical #1 is `LT-2800.3000` / "Home Health Aide Services", pages 1-48. Other
categories use the same markup, so a new taxonomy code is a new lead list with
no new parsing code:

```bash
python3 scrape_mn_homecare.py --taxonomy <code> --label "<Category Name>"
```

Get codes from the directory's own category links. **Never guess a code.**

Each result card holds: provider name (linking to a detail page at
`/adresources/search/<uuid>/`), licence type (Basic / Comprehensive Home Care
License), street address, phone, and an action row with **Call**, **Website**,
**Directions**.

## THE KEY RULE

In the action row, **"Website" is an `<a>` with an href when the provider HAS a
website, and bare text with no anchor when they do NOT.** That presence or
absence of the anchor element is the classification.

- Check for the actual `<a>` element. Never classify on text matching.
- Never invent, guess, or look up a website URL for any provider.
- A dead anchor (`href="#"`, `javascript:`, `tel:`, `mailto:`) is not a website.

## Accuracy — non-negotiable

**The state data lags reality.** A provider listed with no website may have one.
Confirmed: *Evanpro Home Health Care LLC* shows no website on the state listing
but runs `evanprohomecare.com`.

Every row therefore carries `verified = "unverified"`. This is a **shortlist of
candidates to verify**, never confirmed fact. Do not remove that column, and do
not describe the output as confirmed.

## Current status

`scrape_mn_homecare.py` is written and complete. It was built in a cloud session
whose network policy **blocked mn.gov** (`403 to CONNECT`), so:

- The parsing logic is proven against a synthetic fixture.
- **The selectors have NEVER been tested against real mn.gov markup.**

That is the one open item.

## The acceptance test — do this before anything else

Run page 37 only:

```bash
python3 scrape_mn_homecare.py --pages 37
```

Abdul's ground truth for that page. **If the parser disagrees with any of these,
the parser is wrong — fix it and show him again:**

| Provider | City | Expected |
|---|---|---|
| Evanpro Home Health Care LLC | Eagan | NO website |
| Teolak Home Care LLC | Woodbury | NO website |
| Chaska Quality Care LLC | Chaska | HAS website |
| Trusted Home Care LLC | Minneapolis (Cedar Lake) | HAS website |

Also assert **exactly 25 provider cards**:
- Fewer → the card-boundary logic is missing some.
- More → it is picking up something that is not a provider.

Show name, city, phone and has_website for every row.

**If you change any selector, say exactly what you changed and why. Do not
quietly fix it and report success.**

## Rules for the full run

- Only run all 48 pages **after Abdul says go**. Never on your own initiative.
- 1 second between requests. Normal browser User-Agent.
- Retry a failed page 3x with backoff; log a page that still fails, never crash.
- Dedupe by detail-page UUID — providers repeat across pages.
- Write `<slug>-all.csv` (everything) and `<slug>-no-website-leads.csv`
  (`has_website=false` only, **sorted by city** so the Twin Cities metro is
  first), where `<slug>` comes from the category label.
- Print a summary: total found, how many had no website, and a per-city
  breakdown for the top 15 cities.

## Do not

- Do not use Playwright or Selenium. requests + BeautifulSoup only.
- Do not hammer the server.
- Do not scrape any search engine.
- Do not invent or guess a website URL for any provider.
