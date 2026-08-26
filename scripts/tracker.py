#!/usr/bin/env python3
"""
Sale Tracker
------------
Polls each brand's Shopify `products.json` feed (as listed in config/brands.json),
detects new sale items and price drops, and writes docs/data/latest.json for the
dashboard (docs/index.html) to read. Optionally sends Discord / ntfy.sh notifications.

Run manually with:  python scripts/tracker.py
Normally run on a schedule by .github/workflows/track.yml
"""

import json
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "brands.json"
DATA_DIR = ROOT / "docs" / "data"
DB_PATH = ROOT / "data" / "history.db"
LATEST_PATH = DATA_DIR / "latest.json"

HISTORY_RETENTION_DAYS = 120
PRICE_DROP_THRESHOLD_PCT = float(os.environ.get("PRICE_DROP_THRESHOLD_PCT", "5"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PersonalSaleTracker/1.0; personal use)"
}

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

FX_API_URL = "https://api.frankfurter.app/latest"  # free, no API key, ECB reference rates


def get_fx_rates(currencies):
    """Return {currency_code: units_per_1_USD} for every non-USD currency needed.
    Falls back gracefully (returns {}) if the FX API is unreachable — in that case
    non-USD brands will be flagged rather than silently mislabeled as USD."""
    needed = sorted({c for c in currencies if c and c != "USD"})
    if not needed:
        return {}
    try:
        resp = requests.get(
            FX_API_URL,
            params={"from": "USD", "to": ",".join(needed)},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("rates", {})
    except Exception as e:
        print(f"  ! FX rate fetch failed, non-USD prices will be flagged instead of converted: {e}")
        return {}


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True, parents=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS variant_history (
            brand TEXT,
            product_id INTEGER,
            variant_id INTEGER,
            price REAL,
            compare_at_price REAL,
            available INTEGER,
            seen_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS known_products (
            brand TEXT,
            product_id INTEGER,
            first_seen TEXT,
            PRIMARY KEY (brand, product_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vh_lookup ON variant_history (brand, variant_id, seen_at)")
    conn.commit()
    return conn


def prune_history(conn):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM variant_history WHERE seen_at < ?", (cutoff,))
    conn.commit()


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def fetch_products(url):
    """Fetch a Shopify products.json endpoint, handling pagination. Returns
    a list of products, or None if the request failed entirely."""
    all_products = []
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        page_url = f"{url}{sep}limit=250&page={page}"
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=15)
            if resp.status_code == 404 and page == 1:
                return None
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            print(f"  ! Failed to fetch {page_url}: {e}")
            return all_products if all_products else None

        products = payload.get("products", [])
        if not products:
            break
        all_products.extend(products)
        if len(products) < 250:
            break
        page += 1
        time.sleep(1)
    return all_products


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

def notify_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message[:1900]}, timeout=10)
    except Exception as e:
        print(f"  ! Discord notify failed: {e}")


def notify_ntfy(title, message):
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title[:200]},
            timeout=10,
        )
    except Exception as e:
        print(f"  ! ntfy notify failed: {e}")


def notify(title, message):
    print(f"[NOTIFY] {title}: {message}")
    notify_discord(f"**{title}**\n{message}")
    notify_ntfy(title, message)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    brands = load_config()
    conn = init_db()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    dashboard_items = []
    errors = []

    active_brands = [b for b in brands if b.get("enabled", True)]
    fx_rates = get_fx_rates(b.get("currency", "USD") for b in active_brands)

    for brand in active_brands:
        name = brand["name"]
        url = brand["url"]
        store_base = brand.get("store_base") or f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        currency = brand.get("currency", "USD")
        currency_confirmed = brand.get("currency_confirmed", False)

        # units of `currency` per 1 USD; None means we couldn't convert
        if currency == "USD":
            usd_rate = 1.0
        else:
            usd_rate = fx_rates.get(currency)
            if usd_rate is None:
                errors.append(
                    f"{name}: no FX rate available for {currency} this run — "
                    f"prices shown in {currency}, NOT converted to USD."
                )

        print(f"Fetching {name} -> {url}")
        products = fetch_products(url)
        if products is None:
            msg = f"{name}: failed to fetch {url} — endpoint may be down, renamed, or blocked."
            print(f"  ! {msg}")
            errors.append(msg)
            continue

        time.sleep(random.uniform(1, 3))  # be polite between brands

        for product in products:
            product_id = product.get("id")
            title = product.get("title", "Untitled")
            handle = product.get("handle", "")
            product_url = f"{store_base}/products/{handle}"
            images = product.get("images", [])
            image_url = images[0]["src"] if images else (product.get("image") or {}).get("src")
            options = product.get("options", [])
            variants = product.get("variants", [])

            cur.execute(
                "SELECT 1 FROM known_products WHERE brand=? AND product_id=?",
                (name, product_id),
            )
            is_new_product = cur.fetchone() is None
            if is_new_product:
                cur.execute(
                    "INSERT OR IGNORE INTO known_products (brand, product_id, first_seen) VALUES (?,?,?)",
                    (name, product_id, now),
                )

            variant_rows = []
            best_variant = None
            best_discount_pct = -1.0

            for v in variants:
                try:
                    price = float(v.get("price") or 0)
                except (TypeError, ValueError):
                    price = 0.0

                compare_at = v.get("compare_at_price")
                try:
                    compare_at = float(compare_at) if compare_at else None
                except (TypeError, ValueError):
                    compare_at = None

                discount_pct = 0.0
                if compare_at and compare_at > price > 0:
                    discount_pct = round((compare_at - price) / compare_at * 100, 1)

                variant_id = v.get("id")
                available = bool(v.get("available", False))

                # Look up the most recent previously-seen price for this variant
                cur.execute(
                    """SELECT price FROM variant_history
                       WHERE brand=? AND variant_id=?
                       ORDER BY seen_at DESC LIMIT 1""",
                    (name, variant_id),
                )
                row = cur.fetchone()
                prev_price = row[0] if row else None

                if prev_price is not None and price < prev_price:
                    drop_pct = round((prev_price - price) / prev_price * 100, 1) if prev_price else 0
                    if drop_pct >= PRICE_DROP_THRESHOLD_PCT:
                        notify(
                            f"Price drop — {name}",
                            f"{title} ({v.get('title')}) dropped {drop_pct}%: "
                            f"${prev_price:.2f} -> ${price:.2f}\n{product_url}",
                        )

                cur.execute(
                    """INSERT INTO variant_history
                       (brand, product_id, variant_id, price, compare_at_price, available, seen_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (name, product_id, variant_id, price, compare_at, int(available), now),
                )

                price_usd = round(price / usd_rate, 2) if usd_rate else None
                compare_at_usd = round(compare_at / usd_rate, 2) if (usd_rate and compare_at) else None

                vr = {
                    "variant_id": variant_id,
                    "title": v.get("title"),
                    "option1": v.get("option1"),
                    "option2": v.get("option2"),
                    "price": price,
                    "compare_at_price": compare_at,
                    "price_usd": price_usd,
                    "compare_at_price_usd": compare_at_usd,
                    "currency": currency,
                    "discount_pct": discount_pct,
                    "available": available,
                }
                variant_rows.append(vr)
                if discount_pct > best_discount_pct:
                    best_discount_pct = discount_pct
                    best_variant = vr

            # Only surface items that are actually discounted somewhere
            if best_discount_pct <= 0:
                continue

            if is_new_product:
                sizes_note = ", ".join(sorted({v["title"] for v in variant_rows if v["available"]})) or "check site"
                notify(
                    f"New sale item — {name}",
                    f"{title} — up to {best_discount_pct}% off\nAvailable: {sizes_note}\n{product_url}",
                )

            dashboard_items.append({
                "brand": name,
                "title": title,
                "product_url": product_url,
                "image": image_url,
                "is_new": is_new_product,
                "best_discount_pct": best_discount_pct,
                "currency": currency,
                "currency_confirmed": currency_confirmed,
                "fx_converted": usd_rate is not None,
                "msrp": best_variant["compare_at_price"] if best_variant else None,
                "sale_price": best_variant["price"] if best_variant else None,
                "msrp_usd": best_variant["compare_at_price_usd"] if best_variant else None,
                "sale_price_usd": best_variant["price_usd"] if best_variant else None,
                "options": options,
                "variants": variant_rows,
                "last_checked": now,
            })

    prune_history(conn)
    conn.commit()
    conn.close()

    dashboard_items.sort(key=lambda x: x["best_discount_pct"], reverse=True)

    DATA_DIR.mkdir(exist_ok=True, parents=True)
    with open(LATEST_PATH, "w") as f:
        json.dump({
            "generated_at": now,
            "item_count": len(dashboard_items),
            "errors": errors,
            "items": dashboard_items,
        }, f, indent=2)

    print(f"Done. {len(dashboard_items)} discounted items across {len(brands)} brand feeds. "
          f"{len(errors)} feed errors.")


if __name__ == "__main__":
    main()
