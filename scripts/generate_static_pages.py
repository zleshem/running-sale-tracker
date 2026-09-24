#!/usr/bin/env python3
"""
Generates static, crawlable per-brand deal pages from docs/data/latest.json.

Run this AFTER tracker.py in the same workflow step — it only reads the
latest.json tracker.py already writes and produces:

  docs/brands/<brand-slug>.html   one real HTML page per brand
  docs/brands/index.html          a directory linking to every brand page
  docs/sitemap.xml                so search engines can find all of them

No network calls happen here — it's a pure read-json, write-html step,
safe to run every hour right alongside the tracker. Only stdlib is used,
so no new entry is needed in scripts/requirements.txt.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "docs" / "data" / "latest.json"
AFFILIATE_CONFIG = ROOT / "config" / "affiliate.json"
OUT_DIR = ROOT / "docs" / "brands"

# Change this if you attach a custom domain later — it's used for canonical
# links and the sitemap, both of which need an absolute URL.
SITE_BASE_URL = "https://zleshem.github.io/running-sale-tracker"


def slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def load_affiliate_config() -> dict:
    if AFFILIATE_CONFIG.exists():
        cfg = json.loads(AFFILIATE_CONFIG.read_text())
        return {k: v for k, v in cfg.items() if not k.startswith("_")}
    return {}


def wrap_affiliate_link(brand: str, url: str, templates: dict) -> str:
    tmpl = templates.get(brand)
    if not tmpl:
        return url
    return tmpl.replace("{url}", url)


def money(n, currency="USD") -> str:
    if n is None:
        return "—"
    symbols = {"USD": "$", "EUR": "€", "GBP": "£", "SEK": "kr", "HKD": "HK$", "CAD": "C$"}
    sym = symbols.get(currency, currency + " ")
    if currency == "SEK":
        return f"{n:.2f} {sym}"
    return f"{sym}{n:.2f}"


def esc(s) -> str:
    if s is None:
        return ""
    s = str(s)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


PAGE_CSS = """
  :root{ --bg:#0F1B14; --surface:#16261D; --line:#2A3D30; --hivis:#D4FF3D; --hazard:#FF5A36; --chalk:#EDEAE0; --muted:#8FA391; }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--bg); color:var(--chalk); font-family:'Oswald',system-ui,sans-serif; }
  .mono{ font-family:'JetBrains Mono',monospace; }
  a{ color:inherit; }
  header{ padding:24px; border-bottom:1px solid var(--line); }
  header a.back{ font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--muted); text-decoration:none; }
  h1{ margin:10px 0 4px; text-transform:uppercase; letter-spacing:0.03em; }
  .updated{ font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--muted); }
  main{ max-width:900px; margin:0 auto; padding:24px; }
  .item{ display:flex; gap:16px; padding:16px 0; border-bottom:1px solid var(--line); }
  .item img{ width:80px; height:80px; object-fit:cover; border-radius:6px; background:var(--surface); flex-shrink:0; }
  .item .info{ flex:1; }
  .item h2{ font-size:16px; margin:0 0 4px; font-weight:500; }
  .item .sub{ font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--muted); margin-bottom:6px; }
  .item .price{ font-family:'JetBrains Mono',monospace; font-size:14px; }
  .item .price .msrp{ text-decoration:line-through; color:var(--muted); margin-right:6px; }
  .item .discount{ font-family:'JetBrains Mono',monospace; font-weight:700; color:var(--hivis); white-space:nowrap; }
  .go{ display:inline-block; margin-top:8px; font-family:'JetBrains Mono',monospace; font-size:11px; font-weight:700; text-transform:uppercase; text-decoration:none; background:var(--chalk); color:var(--bg); padding:6px 10px; border-radius:5px; }
  ul{ list-style:none; padding:0; }
  li{ padding:10px 0; border-bottom:1px solid var(--line); }
  li a{ font-size:16px; text-decoration:none; }
  li a:hover{ color:var(--hivis); }
  .count{ font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--muted); margin-left:8px; }
"""

BRAND_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body>
<header>
  <a class="back" href="../index.html">&larr; ALL BRANDS (live dashboard)</a>
  <h1>{brand} <span class="mono" style="color:var(--muted); font-size:14px;">sale tracker</span></h1>
  <div class="updated">{count} item(s) on sale &middot; last checked {updated}</div>
</header>
<main>
{items_html}
</main>
</body>
</html>
"""

ITEM_TEMPLATE = """<div class="item">
  {img}
  <div class="info">
    <h2>{title}</h2>
    <div class="sub">{product_type}{new_tag}{lowest_tag}</div>
    <div class="price"><span class="msrp">{msrp}</span>{sale} <span class="discount">-{discount}% off</span></div>
    <a class="go" href="{url}" target="_blank" rel="noopener sponsored">View product &rarr;</a>
  </div>
</div>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Browse brands — Sale Rack</title>
<meta name="description" content="Every independent running and cycling brand we track for real discounts, one page per brand.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body>
<header>
  <a class="back" href="../index.html">&larr; LIVE DASHBOARD</a>
  <h1>Browse by brand</h1>
</header>
<main>
  <ul>
    {links}
  </ul>
</main>
</body>
</html>
"""


def build():
    if not DATA_FILE.exists():
        print(f"No data file at {DATA_FILE} yet — run tracker.py first.", file=sys.stderr)
        return

    data = json.loads(DATA_FILE.read_text())
    items = data.get("items", [])
    updated_str = data.get("generated_at") or datetime.now(timezone.utc).isoformat()
    templates = load_affiliate_config()

    by_brand: dict[str, list] = {}
    for item in items:
        by_brand.setdefault(item["brand"], []).append(item)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index_links = []

    for brand, brand_items in sorted(by_brand.items()):
        slug = slugify(brand)
        brand_items = sorted(brand_items, key=lambda i: i.get("best_discount_pct", 0), reverse=True)

        items_html = []
        for item in brand_items:
            currency = item.get("currency", "USD")
            display_currency = "USD" if (currency == "USD" or item.get("fx_converted")) else currency
            msrp_val = item.get("msrp_usd", item.get("msrp"))
            sale_val = item.get("sale_price_usd", item.get("sale_price"))

            # Prefer a pre-wrapped affiliate_url if tracker.py already sets one;
            # otherwise fall back to wrapping here from config/affiliate.json.
            url = item.get("affiliate_url") or wrap_affiliate_link(brand, item["product_url"], templates)

            new_tag = " &middot; NEW" if item.get("is_new") else ""
            lowest_tag = " &middot; LOWEST EVER" if item.get("is_lowest_ever") else ""

            items_html.append(ITEM_TEMPLATE.format(
                img=f'<img src="{esc(item.get("image",""))}" alt="{esc(item.get("title",""))}" loading="lazy">'
                    if item.get("image") else '<div style="width:80px;height:80px;flex-shrink:0;"></div>',
                title=esc(item.get("title", "Untitled")),
                product_type=esc(item.get("product_type", "Uncategorized")),
                new_tag=new_tag,
                lowest_tag=lowest_tag,
                msrp=money(msrp_val, display_currency),
                sale=money(sale_val, display_currency),
                discount=item.get("best_discount_pct", 0),
                url=esc(url),
            ))

        page_html = BRAND_PAGE_TEMPLATE.format(
            title=f"{brand} sale tracker — real discounts, checked hourly",
            description=f"{len(brand_items)} {brand} item(s) currently on sale, tracked hourly straight from their own storefront.",
            canonical=f"{SITE_BASE_URL}/brands/{slug}.html",
            css=PAGE_CSS,
            brand=esc(brand),
            count=len(brand_items),
            updated=esc(updated_str),
            items_html="\n".join(items_html),
        )

        out_path = OUT_DIR / f"{slug}.html"
        out_path.write_text(page_html, encoding="utf-8")
        index_links.append(
            f'<li><a href="{slug}.html">{esc(brand)}</a><span class="count">{len(brand_items)} on sale</span></li>'
        )
        print(f"wrote {out_path}")

    index_html = INDEX_TEMPLATE.format(css=PAGE_CSS, links="\n    ".join(index_links))
    (OUT_DIR / "index.html").write_text(index_html, encoding="utf-8")
    print(f"wrote {OUT_DIR / 'index.html'}")

    urls = [f"{SITE_BASE_URL}/brands/{slugify(b)}.html" for b in by_brand.keys()]
    urls.append(f"{SITE_BASE_URL}/brands/index.html")
    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        sitemap.append(f"  <url><loc>{u}</loc></url>")
    sitemap.append("</urlset>")
    (ROOT / "docs" / "sitemap.xml").write_text("\n".join(sitemap), encoding="utf-8")
    print(f"wrote {ROOT / 'docs' / 'sitemap.xml'}")


if __name__ == "__main__":
    build()
