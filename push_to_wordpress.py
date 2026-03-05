#!/usr/bin/env python3
"""
push_to_wordpress.py
====================
Pushes the current rates.json to your WordPress site.
Two approaches:

1. Update a WordPress PAGE that contains the shopping widget (recommended)
2. Store rates in WordPress Options API (for use with a custom theme/plugin)

REQUIRES:
  - WordPress Application Password (Settings > Users > Application Passwords)
  - The page must exist with slug 'shop' or 'electricity-rates'

ENV VARS:
  WP_URL          https://amerigyenergy.com
  WP_USER         your-wordpress-username
  WP_APP_PASSWORD xxxx xxxx xxxx xxxx xxxx xxxx
"""

import os
import json
import base64
import requests
from pathlib import Path

WP_URL = os.environ.get("WP_URL", "https://amerigyenergy.com")
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")


def get_auth_header():
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def update_option(option_name: str, value: dict):
    """Store rates in WordPress options table via REST API."""
    # NOTE: Requires a custom plugin endpoint or ACF REST API
    # Standard WP REST API doesn't expose options, so we use a thin plugin.
    endpoint = f"{WP_URL}/wp-json/amerigy/v1/rates"
    r = requests.post(endpoint, json=value, headers=get_auth_header(), timeout=30)
    r.raise_for_status()
    print(f"Option updated: {r.json()}")


def update_page_content(page_id: int, rate_data: dict):
    """Inject rate data into a WordPress page's HTML content."""
    import re
    
    # First, GET the current page content
    r = requests.get(
        f"{WP_URL}/wp-json/wp/v2/pages/{page_id}",
        headers=get_auth_header(),
        timeout=30
    )
    r.raise_for_status()
    page = r.json()
    
    current_content = page["content"]["raw"]
    
    # Replace the RATE_DATA block
    json_str = json.dumps(rate_data, indent=4, default=str)
    pattern = r'(const RATE_DATA = )(\{.*?\});'
    new_content = re.sub(pattern, f'\\1{json_str};', current_content, flags=re.DOTALL)
    
    # PUT updated content back
    r = requests.put(
        f"{WP_URL}/wp-json/wp/v2/pages/{page_id}",
        json={"content": new_content},
        headers=get_auth_header(),
        timeout=30
    )
    r.raise_for_status()
    print(f"Page {page_id} updated successfully")


def find_page_by_slug(slug: str) -> int | None:
    """Find WordPress page ID by slug."""
    r = requests.get(
        f"{WP_URL}/wp-json/wp/v2/pages?slug={slug}",
        headers=get_auth_header(),
        timeout=30
    )
    r.raise_for_status()
    pages = r.json()
    return pages[0]["id"] if pages else None


if __name__ == "__main__":
    rates_file = Path("rates.json")
    if not rates_file.exists():
        print("ERROR: rates.json not found — run amerigy_rate_scraper.py first")
        exit(1)
    
    rate_data = json.loads(rates_file.read_text())
    
    # Find the shopping page
    page_id = find_page_by_slug("electricity-rates") or find_page_by_slug("shop")
    
    if page_id:
        update_page_content(page_id, rate_data)
    else:
        print("Page not found. Creating new page...")
        html = Path("index.html").read_text() if Path("index.html").exists() else ""
        r = requests.post(
            f"{WP_URL}/wp-json/wp/v2/pages",
            json={
                "title": "Shop Electricity Rates",
                "slug": "electricity-rates",
                "content": html,
                "status": "publish"
            },
            headers=get_auth_header(),
            timeout=30
        )
        r.raise_for_status()
        print(f"Created page: {r.json()['link']}")
