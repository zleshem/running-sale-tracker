# Sale Rack Tracker

Polls a list of running-gear brands' public Shopify `products.json` feeds on a schedule,
tracks price history in SQLite, flags **new sale items** and **price drops**, and renders
a dashboard sorted by biggest discount off MSRP — hosted free on GitHub Pages, updated
automatically by GitHub Actions. No server, no computer that needs to stay on.

## What's in here

```
config/brands.json         <- the list of brands/URLs to track. Edit this to add more.
scripts/tracker.py         <- fetches feeds, diffs against history, writes dashboard data
scripts/requirements.txt   <- Python deps (just `requests`)
.github/workflows/track.yml <- runs tracker.py every hour, commits the result
docs/index.html            <- the dashboard (served by GitHub Pages)
docs/data/latest.json      <- generated automatically, dashboard reads this
data/history.db            <- SQLite price history (used to detect drops/new items)
```

## One-time setup (~10 minutes)

1. **Create a new GitHub repo** (private is fine — Pages works on private repos too,
   though Actions minutes are metered on private repos; see note below).

2. **Upload all these files** to the repo, preserving the folder structure exactly
   (the `.github/workflows/track.yml` path matters — GitHub only picks up workflows
   from that exact location).

3. **Enable GitHub Pages**:
   Repo → Settings → Pages → "Build and deployment" → Source: **Deploy from a branch**
   → Branch: `main`, folder: **/docs** → Save.
   GitHub will give you a URL like `https://yourusername.github.io/sale-tracker/` —
   that's your dashboard.

4. **(Optional but recommended) Set up notifications** — Settings → Secrets and
   variables → Actions → New repository secret:
   - `DISCORD_WEBHOOK_URL` — a Discord channel webhook URL (Discord → Channel Settings
     → Integrations → Webhooks → New Webhook → Copy URL). Free, instant, works great
     from your phone if you have the Discord app.
   - `NTFY_TOPIC` — any random topic name (e.g. `mysalealerts-8x2f`) for
     [ntfy.sh](https://ntfy.sh) — free push notifications with zero signup, just
     install the ntfy app and subscribe to the same topic name.
   You can set up one, both, or neither — the script skips whichever isn't configured.

5. **Trigger a first run manually** so you don't have to wait for the hourly schedule:
   Repo → Actions tab → "Track Sales" workflow → "Run workflow" button.
   After it finishes (~1-2 min), refresh your Pages URL and you should see items.

That's it — from here it runs on its own every hour.

## Adding a new brand later

Open `config/brands.json` and add an entry:

```json
{
  "name": "New Brand",
  "store_base": "https://newbrand.com",
  "url": "https://newbrand.com/collections/sale/products.json",
  "enabled": true
}
```

To find the right URL for a new brand: go to their sale/clearance collection page,
copy the URL, and append `/products.json` to it. If that returns valid JSON in your
browser, you're good. If it 404s, that store has the endpoint disabled and can't be
tracked this way. Set `"enabled": false` on any brand you want to pause without
deleting it.

Commit the change (or edit the file directly on github.com) — the next scheduled run
picks it up automatically, no other changes needed.

## How "new item" and "price drop" detection works

- Every run, each product's current price is compared to the last price seen for that
  exact variant (size/color combo). A drop of 5%+ (adjustable via `PRICE_DROP_THRESHOLD_PCT`
  in the workflow file) triggers a notification.
- A product is flagged "new" the first time its Shopify product ID appears in a given
  brand's feed — not by title, so a renamed listing won't falsely trigger.
- The dashboard shows the single best (deepest) discount per product, but all
  size/color variants and their individual availability are included in the data if
  you want to extend the UI later.

## Currency handling

Every brand entry in `config/brands.json` has a `"currency"` field. On each run, the
script fetches live exchange rates (from [frankfurter.app](https://www.frankfurter.app/),
free, no key needed) and converts everything to USD for display, while still keeping the
original native price around. If a rate can't be fetched, that brand's items are shown
in their **native currency with a visible warning** instead of being silently mislabeled
as USD.

`"currency_confirmed": true/false` tracks how sure I am about each brand's actual base
currency:

- **Confirmed USD** (Tracksmith, Bandit, District Vision, Path Projects, Oiselle, Janji) —
  US-native brands, no conversion needed.
- **Assumed USD, not verified** (Craft, Ciele) — very likely USD given their US
  fulfillment, but I haven't confirmed the raw feed itself returns USD.
- **Non-USD, best-guess currency** (Satisfy → EUR, UNNA → SEK, Rendezvous → GBP,
  9pt9 → HKD) — based on each company's home country. These get converted, but the
  *conversion is only as good as the currency guess* — if a brand's actual base
  currency differs from what's set here, fix it directly in `brands.json`.

**To actually verify a brand's currency yourself**: open that brand's `products.json`
URL in a browser and look at a price you already know from browsing the live site. If
the number in the JSON is roughly 10x higher than the USD price you saw on the site,
you're likely looking at SEK; if it's a plausible number but the site showed you a
different price, it's likely a different currency entirely (EUR/GBP tend to be close in
magnitude to USD, which makes them easy to miss).

## Things worth knowing / limitations

- **Endpoint stability**: brands rename sale collections (e.g. `winter-26-sale` →
  `spring-27-sale`) without warning, which breaks that URL. The dashboard shows a red
  "FEED ISSUES" line when a fetch fails — check `config/brands.json` and update the URL
  when that happens.
- **MSRP accuracy**: the "discount %" depends entirely on the brand having set
  `compare_at_price` correctly. A few brands leave it blank even on marked-down items
  (no discount will show), and a few inflate it. Treat it as what the brand claims,
  not verified ground truth.
- **Currency**: all URLs are pinned to each brand's primary USD-facing storefront.
  If a brand ever changes their default currency behavior, prices in the dashboard
  could drift from what you'd actually be charged — worth spot-checking occasionally.
- **Politeness**: the script waits 1–3 seconds between brands and runs hourly by
  default. Don't drop the schedule below every 15 minutes or so — it's unnecessary for
  this use case and increases the chance of getting rate-limited or blocked.
- **Public repo vs private repo**: GitHub Actions is unlimited/free on public repos.
  On private repos you get 2,000 free minutes/month, which is more than enough for an
  hourly job like this (roughly 1-2 min/run × 24 runs/day ≈ 30-50 min/day), but worth
  knowing if you ever add many more brands or run more frequently.
- **Repo growth**: `data/history.db` grows a little each run. The script automatically
  prunes price history older than 120 days, so long-term growth is capped, but the
  file will still grow gradually over months — not a concern at this scale, just
  noting it so it's not a surprise years from now.
- **This isn't a guarantee of stock/price at checkout** — always confirmed by the
  brand's live site, especially the "sale price already includes duties" caveat we
  discussed earlier for the European brands in this list.
