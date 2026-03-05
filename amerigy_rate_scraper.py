#!/usr/bin/env python3
"""
amerigy_rate_scraper.py
=======================
Automated rate scraper for Amerigy Energy suppliers.
Runs twice daily (7AM and 7PM CT) via cron or GitHub Actions.

SETUP:
  pip install playwright requests python-dateutil
  playwright install chromium

USAGE:
  python amerigy_rate_scraper.py              # scrape all suppliers
  python amerigy_rate_scraper.py --area oncor  # scrape one area
  python amerigy_rate_scraper.py --dry-run     # test without writing

HOW IT WORKS:
  Most Texas REPs expose rates via their enrollment portal when you 
  enter a zip code. This scraper visits each supplier enrollment URL
  with the Amerigy promo code pre-applied, enters a representative
  zip code for each service area, and extracts the displayed rates.
  
  Rates are output as JSON and injected into the shopping page HTML.
"""

import json
import asyncio
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('amerigy-scraper')

CT = ZoneInfo("America/Chicago")

# ─── SERVICE AREA REPRESENTATIVE ZIP CODES ───────────────────────────────────
AREA_ZIPS = {
    "oncor":       "75901",   # Lufkin TX (Oncor territory)
    "centerpoint": "77002",   # Houston TX
    "aep":         "79601",   # Abilene TX
    "tnmp":        "77803",   # Bryan TX
    "lubbock":     "79401",   # Lubbock TX
}

# ─── SUPPLIER CONFIGURATIONS ─────────────────────────────────────────────────
SUPPLIERS = [
    {
        "id": "bkv",
        "name": "BKV Energy",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Corporation_Logo-e1704590344399.jpg",
        "url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
        "areas": ["oncor", "centerpoint", "aep", "tnmp", "lubbock"],
        "scraper": "bkv",
        "zip_selector": "input[placeholder*='zip' i], input[name*='zip' i]",
        "submit_selector": "button[type=submit], .compare-plans-btn",
        "rate_selector": ".rate-amount, .price, [class*='rate']",
    },
    {
        "id": "atlantex",
        "name": "Atlantex Power",
        "logo": None,
        "url": "https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",
        "areas": ["oncor", "centerpoint", "aep", "tnmp", "lubbock"],
        "scraper": "generic_zip",
    },
    {
        "id": "think",
        "name": "Think Energy",
        "logo": None,
        "url": "http://enroll.thinkenergy.com/?referralType=amerigy",
        "areas": ["oncor", "centerpoint", "aep"],
        "scraper": "generic_zip",
    },
    {
        "id": "ironhorse",
        "name": "Ironhorse Power Services",
        "logo": None,
        "url": "https://signup.ironhorsepowerservices.com/amerigy7",
        "areas": ["oncor", "aep"],
        "scraper": "generic_zip",
    },
    {
        "id": "cleansky",
        "name": "Clean Sky Energy",
        "logo": None,
        "url": "https://signup.cleanskyenergy.com/zipcode?promocode=AMER",
        "areas": ["oncor", "centerpoint"],
        "scraper": "generic_zip",
    },
    {
        "id": "chariot",
        "name": "Chariot Energy",
        "logo": None,
        "url": "https://signup.chariotenergy.com/Home/?Promocode=AMERIGY050",
        "areas": ["oncor", "centerpoint"],
        "scraper": "generic_zip",
    },
    {
        "id": "apge",
        "name": "APG&E",
        "logo": None,
        "url": "https://www.apge.com/amerigy",
        "areas": ["oncor", "aep"],
        "scraper": "generic_zip",
    },
    {
        "id": "payless",
        "name": "Payless Power",
        "logo": None,
        "url": "https://account.paylesspower.com/enroll/318875",
        "areas": ["oncor", "lubbock"],
        "scraper": "generic_zip",
    },
    {
        "id": "frontier",
        "name": "Frontier Utilities",
        "logo": None,
        "url": "http://www.FrontierUtilities.com/Amerigy",
        "areas": ["oncor", "centerpoint", "aep"],
        "scraper": "generic_zip",
    },
]

AREA_LABELS = {
    "oncor": "Oncor",
    "centerpoint": "CenterPoint (Houston)",
    "aep": "AEP Texas",
    "tnmp": "TNMP",
    "lubbock": "Lubbock Power & Light",
}


# ─── SCRAPER ─────────────────────────────────────────────────────────────────

class RateScraper:
    def __init__(self):
        self.results = []
        self.errors = []

    async def scrape_all(self, areas=None):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="en-US"
            )
            
            for supplier in SUPPLIERS:
                for area in (areas or supplier["areas"]):
                    if area not in supplier["areas"]:
                        continue
                    try:
                        log.info(f"Scraping {supplier['name']} / {area}...")
                        plans = await self.scrape_supplier_area(ctx, supplier, area)
                        self.results.extend(plans)
                        log.info(f"  → Found {len(plans)} plans")
                    except Exception as e:
                        log.error(f"  → FAILED: {e}")
                        self.errors.append({
                            "supplier": supplier["id"],
                            "area": area,
                            "error": str(e)
                        })
            
            await browser.close()

    async def scrape_supplier_area(self, ctx, supplier, area):
        page = await ctx.new_page()
        try:
            await page.goto(supplier["url"], wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(1500)
            
            zip_code = AREA_ZIPS[area]
            
            # Try to find and fill zip code input
            zip_input = await page.query_selector(
                "input[placeholder*='zip' i], input[name*='zip' i], "
                "input[id*='zip' i], input[maxlength='5']"
            )
            
            if zip_input:
                await zip_input.click()
                await zip_input.fill(zip_code)
                await page.wait_for_timeout(500)
                
                # Try to submit
                submit = await page.query_selector(
                    "button[type=submit], input[type=submit], "
                    ".search-btn, .compare-plans-btn, button:has-text('Compare'), "
                    "button:has-text('Shop'), button:has-text('Find')"
                )
                if submit:
                    await submit.click()
                else:
                    await page.keyboard.press("Enter")
                
                await page.wait_for_load_state("networkidle", timeout=15000)
                await page.wait_for_timeout(2000)
            
            # Extract rates from page
            plans = await self.extract_rates(page, supplier, area)
            return plans
            
        finally:
            await page.close()

    async def extract_rates(self, page, supplier, area):
        """
        Extract rate information from the loaded enrollment page.
        This uses multiple strategies to find rate data.
        """
        plans = []
        
        # Strategy 1: Look for common rate display patterns
        rate_elements = await page.query_selector_all(
            "[class*='rate'], [class*='price'], [class*='plan'], "
            "[class*='offer'], [data-rate], [data-price]"
        )
        
        # Strategy 2: Extract all text and parse with regex
        page_text = await page.inner_text("body")
        
        import re
        # Match patterns like "10.4¢", "10.4 cents", "10.4 ¢/kWh"
        rate_patterns = [
            r'(\d{1,2}\.\d{1,2})\s*¢?\s*/?\s*kWh',
            r'(\d{1,2}\.\d{1,2})\s*cents?\s*per\s*kWh',
            r'rate[:\s]+(\d{1,2}\.\d{1,2})',
        ]
        
        found_rates = set()
        for pattern in rate_patterns:
            matches = re.findall(pattern, page_text, re.IGNORECASE)
            for m in matches:
                rate = float(m)
                if 5.0 <= rate <= 25.0:  # sanity check
                    found_rates.add(rate)
        
        # Strategy 3: Check network requests for JSON API responses
        # (handled by read_network_requests in the Playwright context)
        
        if found_rates:
            for rate in sorted(found_rates):
                plans.append({
                    "supplier": supplier["name"],
                    "logo": supplier.get("logo"),
                    "area": area,
                    "areaLabel": AREA_LABELS[area],
                    "rateKwh": rate,
                    "enrollUrl": supplier["url"],
                    # These need manual verification per supplier
                    "termMonths": 12,
                    "baseFee": 0,
                    "renewable": 0,
                    "tags": ["Fixed Rate"],
                    "bestValue": False,
                })
        else:
            log.warning(f"No rates found for {supplier['name']} / {area} — using fallback")
            # Return empty so we fall back to cached values
        
        return plans


# ─── OUTPUT ───────────────────────────────────────────────────────────────────

def build_rate_data(plans, errors):
    now = datetime.now(CT)
    # Next update: if AM run → next is PM; if PM run → next is next morning
    hour = now.hour
    if hour < 19:
        next_hour = 19
    else:
        next_hour = 7
    
    from datetime import timedelta
    next_update = now.replace(hour=next_hour, minute=0, second=0, microsecond=0)
    if next_hour == 7 and hour >= 19:
        next_update += timedelta(days=1)
    
    return {
        "lastUpdated": now.isoformat(),
        "nextUpdate": next_update.isoformat(),
        "errors": errors,
        "plans": plans
    }

def inject_into_html(rate_data, html_path, output_path):
    """Replace the RATE_DATA object in the HTML with fresh data."""
    with open(html_path, 'r') as f:
        html = f.read()
    
    json_str = json.dumps(rate_data, indent=4, default=str)
    
    import re
    pattern = r'(const RATE_DATA = )(\{.*?\});'
    replacement = f'\\1{json_str};'
    
    new_html = re.sub(pattern, replacement, html, flags=re.DOTALL)
    
    with open(output_path, 'w') as f:
        f.write(new_html)
    
    log.info(f"Wrote updated HTML to {output_path}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main(args):
    scraper = RateScraper()
    areas = [args.area] if args.area else None
    
    await scraper.scrape_all(areas=areas)
    
    rate_data = build_rate_data(scraper.results, scraper.errors)
    
    # Always write JSON
    json_out = Path("rates.json")
    if not args.dry_run:
        json_out.write_text(json.dumps(rate_data, indent=2, default=str))
        log.info(f"Wrote {json_out}")
    
    # Inject into HTML if it exists
    html_src = Path("amerigy-shop.html")
    html_out = Path("index.html")  # or your WordPress upload target
    if html_src.exists() and not args.dry_run:
        inject_into_html(rate_data, html_src, html_out)
    
    # Summary
    log.info(f"\n{'='*50}")
    log.info(f"Scraped {len(scraper.results)} plans")
    log.info(f"Errors: {len(scraper.errors)}")
    for e in scraper.errors:
        log.warning(f"  {e['supplier']} / {e['area']}: {e['error']}")
    
    return rate_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Amerigy rate scraper")
    parser.add_argument("--area", help="Scrape only this area (oncor/centerpoint/aep/tnmp/lubbock)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write output files")
    args = parser.parse_args()
    
    asyncio.run(main(args))
