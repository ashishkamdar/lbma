# prismx-lbma-fix

A tiny public repo that fetches the **London Bullion Market Association (LBMA)
Fix prices** twice on each London trading day and publishes the result as a
single JSON file used by [prismx.com](https://prismx.com)'s metal-rates page.

## Why this lives here

- LBMA publishes its daily auction results in machine-readable JSON at
  `https://prices.lbma.org.uk/json/`. The values are public benchmark facts.
- We run the fetch on **GitHub Actions** (public repo, unlimited minutes) so it
  costs nothing on a forever basis.
- The output JSON is served as a static file via GitHub Pages so the prismx.com
  frontend fetches it directly from a CDN — zero load on PrismX's servers.

## Output schema

`london-fix.json` (regenerated only when LBMA publishes a newer fix):

```json
{
  "fetched_at": "2026-05-11T15:35:12+00:00",
  "latest_fix_date": "2026-05-11",
  "currency": "USD",
  "unit": "troy_ounce",
  "source": "LBMA (prices.lbma.org.uk)",
  "fixes": {
    "gold":      { "am": { "usd": 4657.50, "date": "2026-05-11" },
                   "pm": { "usd": 4661.20, "date": "2026-05-11" } },
    "silver":    { "noon": { "usd": 80.13, "date": "2026-05-11" } },
    "platinum":  { "am": { "usd": 2031.50, "date": "2026-05-11" },
                   "pm": { "usd": 2035.00, "date": "2026-05-11" } },
    "palladium": { "am": { "usd": 1477.50, "date": "2026-05-11" },
                   "pm": { "usd": 1480.50, "date": "2026-05-11" } }
  }
}
```

`pm` values are `null` on days where the PM auction has not yet closed.

## Schedule

The fetcher runs at four UTC times (covering BST/GMT switches):

- `10:05 UTC` and `11:05 UTC` weekdays — post-AM-Fix window
- `14:35 UTC` and `15:35 UTC` weekdays — post-PM-Fix window

The script is idempotent: if LBMA has not published a newer value, no commit is
made.
