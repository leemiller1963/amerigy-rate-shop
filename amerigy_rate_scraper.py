#!/usr/bin/env python3
"""
Amerigy Energy Rate Scraper
Pulls live rates from the Power to Choose API (api.powertochoose.org).
This is the PUCT's official public rate database — no auth required,
no bot detection, works reliably from GitHub Actions.

Falls back to manual rates if the API is unreachable.
Outputs rates.json for the shop.amerigyenergy.com page.
"""

import json
import requests
import time
from datetime import datetime, timezone

# ── POWER TO CHOOSE API ────────────────────────────────────────────────────────
PTC_API = "https://api.powertochoose.org/api/PowerToChoose/plans"

# One representative ZIP per service area
SERVICE_AREA_ZIPS = {
    "oncor":       "75901",   # Lufkin — Oncor (DFW / East Texas)
    "centerpoint": "77002",   # Houston — CenterPoint
    "aep":         "79601",   # Abilene — AEP (West / South Texas)
    "tnmp":        "76528",   # Gatesville — TNMP
    "lubbock":     "79401",   # Lubbock — Lubbock Power & Light
}

SERVICE_AREA_LABELS = {
    "oncor":       "Oncor (DFW / East Texas)",
    "centerpoint": "CenterPoint (Houston)",
    "aep":         "AEP (West / South Texas)",
    "tnmp":        "TNMP (Bryan / New Braunfels)",
    "lubbock":     "Lubbock Power & Light",
}

# ── SUPPLIER CONFIGURATION ─────────────────────────────────────────────────────
# Keys are lowercase substrings matched against API company_name field.
SUPPLIER_CONFIG = {
    "bkv energy": {
        "display_name": "BKV Energy",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png",
        "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
    },
    "ap gas & electric": {
        "display_name": "APG&E",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg",
        "enroll_url": "https://www.apge.com/amerigy",
    },
    "apg&e": {
        "display_name": "APG&E",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg",
        "enroll_url": "https://www.apge.com/amerigy",
    },
    "chariot energy": {
        "display_name": "Chariot Energy",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/chariot.png",
        "enroll_url": "https://signup.chariotenergy.com/Home/?Promocode=AMERIGY050",
        "renewable_pct_override": 100,
    },
    "payless power": {
        "display_name": "Payless Power",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2021/11/payless-power-logo.png",
        "enroll_url": "https://account.paylesspower.com/enroll/318875",
    },
    "frontier utilities": {
        "display_name": "Frontier Utilities",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/Frontier-Utilities.jpg",
        "enroll_url": "http://www.FrontierUtilities.com/Amerigy",
    },
    "atlantex": {
        "display_name": "Atlantex Power",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/ae-texas-temp-logo.png",
        "enroll_url": "https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",
    },
    "clean sky energy": {
        "display_name": "Clean Sky Energy",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2025/02/logo.svg",
        "enroll_url": "https://signup.cleanskyenergy.com/zipcode?promocode=AMER",
        "renewable_pct_override": 100,
    },
    "think energy": {
        "display_name": "Think Energy",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2024/08/think-ntx.e2c6be7f.png",
        "enroll_url": "http://enroll.thinkenergy.com/?referralType=amerigy",
        "base_fee_note": "+$4.95/mo base fee",
    },
    "ironhorse power": {
        "display_name": "Ironhorse Power",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/Ironhorse.svg",
        "enroll_url": "https://signup.ironhorsepowerservices.com/Amerigy",
    },
}


def match_supplier(company_name):
    """Match a Power to Choose company_name to our supplier config."""
    cn = (company_name or "").lower().strip()
    for key, config in SUPPLIER_CONFIG.items():
        if key in cn:
            return config
    return None


def fetch_ptc_plans(zip_code, area_key):
    """Fetch all plans from Power to Choose for a given ZIP code.
    
    Uses POST to the search endpoint which returns the full plan database,
    not just featured/sponsored plans like the GET endpoint.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://www.powertochoose.org",
        "Referer": "https://www.powertochoose.org/",
    }

    all_plans = []
    page = 1

    while True:
        payload = {
            "zip_code": zip_code,
            "key": "",
            "efficiency_type": "0",
            "renewable_energy_id": "0",
            "time_of_use": "0",
            "prepaid": "0",
            "plan_type": "0",
            "page_number": page,
            "er": "0",
            "isEFLneeded": "0",
        }
        try:
            r = requests.post(PTC_API, json=payload, headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    PTC fetch error (ZIP {zip_code}, page {page}): {e}")
            break

        records = data.get("data", [])
        if not records:
            break

        all_plans.extend(records)

        record_count = data.get("recordCount", 0)
        print(f"    Page {page}: {len(records)} plans (total: {len(all_plans)} of {record_count})")
        if len(all_plans) >= record_count or len(records) == 0:
            break
        page += 1
        time.sleep(0.3)

    return all_plans


def process_ptc_plans(raw_plans, area_key):
    """Filter and format Power to Choose plans for our 9 suppliers."""
    best = {}  # (display_name, term) -> plan dict (keep lowest rate)

    for p in raw_plans:
        company = p.get("company_name", "")
        config = match_supplier(company)
        if not config:
            continue

        term = int(p.get("term_value") or 0)
        if term <= 0:
            continue

        # price_kwh = all-in rate at 2000 kWh in cents
        try:
            rate = round(float(p.get("price_kwh") or 0), 1)
        except (TypeError, ValueError):
            continue
        if rate <= 0:
            continue

        key = (config["display_name"], term, area_key)
        if key in best and rate >= best[key]["rate"]:
            continue  # keep the lower rate

        renewable = (
            config.get("renewable_pct_override")
            or int(p.get("renewable_energy_credit") or 0)
        )

        plan = {
            "supplier":      config["display_name"],
            "term":          term,
            "rate":          rate,
            "renewable_pct": renewable,
            "enroll_url":    config["enroll_url"],
            "logo":          config["logo"],
            "service_area":  area_key,
            "plan_name":     p.get("plan_name", ""),
            "source":        "powertochoose",
        }
        if "base_fee_note" in config:
            plan["base_fee_note"] = config["base_fee_note"]

        best[key] = plan

    return list(best.values())


# ── MANUAL FALLBACK RATES ──────────────────────────────────────────────────────
# Used only if Power to Choose API is unreachable.
# Last verified: March 2026, Oncor service area at 2000 kWh/mo.
FALLBACK_PLANS = [
    # BKV Energy
    {"supplier": "BKV Energy", "term": 6,  "rate": 14.7, "renewable_pct": 0,
     "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 7,  "rate": 14.7, "renewable_pct": 0,
     "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 8,  "rate": 14.5, "renewable_pct": 0,
     "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 9,  "rate": 14.4, "renewable_pct": 0,
     "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 12, "rate": 14.7, "renewable_pct": 0,
     "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 15, "rate": 14.7, "renewable_pct": 0,
     "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 18, "rate": 15.7, "renewable_pct": 0,
     "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 24, "rate": 15.8, "renewable_pct": 0,
     "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 36, "rate": 15.7, "renewable_pct": 0,
     "enroll_url": "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    # APG&E
    {"supplier": "APG&E", "term": 3,  "rate": 11.3, "renewable_pct": 6,
     "enroll_url": "https://www.apge.com/amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg"},
    {"supplier": "APG&E", "term": 6,  "rate": 14.5, "renewable_pct": 6,
     "enroll_url": "https://www.apge.com/amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg"},
    {"supplier": "APG&E", "term": 12, "rate": 14.0, "renewable_pct": 6,
     "enroll_url": "https://www.apge.com/amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg"},
    {"supplier": "APG&E", "term": 15, "rate": 13.8, "renewable_pct": 6,
     "enroll_url": "https://www.apge.com/amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg"},
    {"supplier": "APG&E", "term": 18, "rate": 14.4, "renewable_pct": 6,
     "enroll_url": "https://www.apge.com/amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg"},
    {"supplier": "APG&E", "term": 24, "rate": 14.3, "renewable_pct": 6,
     "enroll_url": "https://www.apge.com/amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg"},
    {"supplier": "APG&E", "term": 36, "rate": 14.5, "renewable_pct": 6,
     "enroll_url": "https://www.apge.com/amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg"},
    # Chariot Energy
    {"supplier": "Chariot Energy", "term": 12, "rate": 13.8, "renewable_pct": 100,
     "enroll_url": "https://signup.chariotenergy.com/Home/?Promocode=AMERIGY050",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/chariot.png"},
    {"supplier": "Chariot Energy", "term": 15, "rate": 14.9, "renewable_pct": 100,
     "enroll_url": "https://signup.chariotenergy.com/Home/?Promocode=AMERIGY050",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/chariot.png"},
    {"supplier": "Chariot Energy", "term": 18, "rate": 14.8, "renewable_pct": 100,
     "enroll_url": "https://signup.chariotenergy.com/Home/?Promocode=AMERIGY050",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/chariot.png"},
    {"supplier": "Chariot Energy", "term": 24, "rate": 13.9, "renewable_pct": 100,
     "enroll_url": "https://signup.chariotenergy.com/Home/?Promocode=AMERIGY050",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/chariot.png"},
    {"supplier": "Chariot Energy", "term": 36, "rate": 14.2, "renewable_pct": 100,
     "enroll_url": "https://signup.chariotenergy.com/Home/?Promocode=AMERIGY050",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/chariot.png"},
    # Clean Sky Energy
    {"supplier": "Clean Sky Energy", "term": 6,  "rate": 14.0, "renewable_pct": 100,
     "enroll_url": "https://signup.cleanskyenergy.com/zipcode?promocode=AMER",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2025/02/logo.svg"},
    {"supplier": "Clean Sky Energy", "term": 12, "rate": 14.0, "renewable_pct": 100,
     "enroll_url": "https://signup.cleanskyenergy.com/zipcode?promocode=AMER",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2025/02/logo.svg"},
    {"supplier": "Clean Sky Energy", "term": 24, "rate": 14.0, "renewable_pct": 100,
     "enroll_url": "https://signup.cleanskyenergy.com/zipcode?promocode=AMER",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2025/02/logo.svg"},
    # Atlantex Power
    {"supplier": "Atlantex Power", "term": 12, "rate": 14.9, "renewable_pct": 0,
     "enroll_url": "https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/ae-texas-temp-logo.png"},
    {"supplier": "Atlantex Power", "term": 15, "rate": 13.8, "renewable_pct": 0,
     "enroll_url": "https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/ae-texas-temp-logo.png"},
    {"supplier": "Atlantex Power", "term": 24, "rate": 14.5, "renewable_pct": 0,
     "enroll_url": "https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/ae-texas-temp-logo.png"},
    {"supplier": "Atlantex Power", "term": 36, "rate": 14.8, "renewable_pct": 0,
     "enroll_url": "https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/ae-texas-temp-logo.png"},
    # Think Energy
    {"supplier": "Think Energy", "term": 12, "rate": 14.3, "renewable_pct": 0,
     "base_fee_note": "+$4.95/mo base fee",
     "enroll_url": "http://enroll.thinkenergy.com/?referralType=amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/08/think-ntx.e2c6be7f.png"},
    {"supplier": "Think Energy", "term": 36, "rate": 15.0, "renewable_pct": 0,
     "base_fee_note": "+$4.95/mo base fee",
     "enroll_url": "http://enroll.thinkenergy.com/?referralType=amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/08/think-ntx.e2c6be7f.png"},
    # Frontier Utilities
    {"supplier": "Frontier Utilities", "term": 12, "rate": 15.8, "renewable_pct": 0,
     "enroll_url": "http://www.FrontierUtilities.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/Frontier-Utilities.jpg"},
    {"supplier": "Frontier Utilities", "term": 24, "rate": 16.0, "renewable_pct": 0,
     "enroll_url": "http://www.FrontierUtilities.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/Frontier-Utilities.jpg"},
    # Payless Power
    {"supplier": "Payless Power", "term": 6,  "rate": 16.7, "renewable_pct": 0,
     "enroll_url": "https://account.paylesspower.com/enroll/318875",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/11/payless-power-logo.png"},
    {"supplier": "Payless Power", "term": 12, "rate": 16.7, "renewable_pct": 0,
     "enroll_url": "https://account.paylesspower.com/enroll/318875",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/11/payless-power-logo.png"},
    # Ironhorse Power
    {"supplier": "Ironhorse Power", "term": 3,  "rate": 13.0, "renewable_pct": 0,
     "enroll_url": "https://signup.ironhorsepowerservices.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/Ironhorse.svg"},
    {"supplier": "Ironhorse Power", "term": 6,  "rate": 15.7, "renewable_pct": 0,
     "enroll_url": "https://signup.ironhorsepowerservices.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/Ironhorse.svg"},
    {"supplier": "Ironhorse Power", "term": 9,  "rate": 15.1, "renewable_pct": 0,
     "enroll_url": "https://signup.ironhorsepowerservices.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/Ironhorse.svg"},
    {"supplier": "Ironhorse Power", "term": 12, "rate": 15.1, "renewable_pct": 0,
     "enroll_url": "https://signup.ironhorsepowerservices.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/Ironhorse.svg"},
    {"supplier": "Ironhorse Power", "term": 15, "rate": 14.8, "renewable_pct": 0,
     "enroll_url": "https://signup.ironhorsepowerservices.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/Ironhorse.svg"},
    {"supplier": "Ironhorse Power", "term": 24, "rate": 15.5, "renewable_pct": 0,
     "enroll_url": "https://signup.ironhorsepowerservices.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/Ironhorse.svg"},
    {"supplier": "Ironhorse Power", "term": 36, "rate": 15.7, "renewable_pct": 0,
     "enroll_url": "https://signup.ironhorsepowerservices.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/Ironhorse.svg"},
]


# ── MAIN BUILD FUNCTION ────────────────────────────────────────────────────────

def build_rates_json():
    all_live_plans = []
    live_count = 0
    failed_areas = []

    print("Fetching live rates from Power to Choose API...")

    for area_key, zip_code in SERVICE_AREA_ZIPS.items():
        label = SERVICE_AREA_LABELS[area_key]
        print(f"  Fetching {label} (ZIP {zip_code})...")

        raw = fetch_ptc_plans(zip_code, area_key)
        if not raw:
            failed_areas.append(area_key)
            print(f"  ✗ {label}: no data returned")
            continue

        matched = process_ptc_plans(raw, area_key)
        all_live_plans.extend(matched)
        live_count += len(matched)

        found = sorted(set(p["supplier"] for p in matched))
        # Debug: show actual company names from API on first area
        if area_key == "oncor" and not matched:
            all_companies = sorted(set(p.get("company_name","") for p in raw))
            print(f"    DEBUG company names in API response: {all_companies}")
        print(f"  ✓ {label}: {len(raw)} total plans, {len(matched)} matched ({', '.join(found) if found else 'none of our suppliers found'})")

        time.sleep(0.5)

    if all_live_plans:
        plans = all_live_plans
        print(f"\n✓ Using live Power to Choose data: {len(plans)} plans")
    else:
        print("\n✗ All PTC fetches failed — using full manual fallback")
        plans = FALLBACK_PLANS.copy()

    plans.sort(key=lambda x: (x["rate"], x["term"]))

    now = datetime.now(timezone.utc)
    areas_live = [SERVICE_AREA_LABELS[k] for k in SERVICE_AREA_LABELS if k not in failed_areas]
    areas_fallback = [SERVICE_AREA_LABELS[k] for k in failed_areas]

    output = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_display": now.strftime("%I:%M %p UTC, %B %d, %Y").lstrip("0").replace(" 0", " "),
        "source": "powertochoose" if all_live_plans else "fallback",
        "service_areas_live": areas_live,
        "service_areas_fallback": areas_fallback,
        "usage_kwh": 2000,
        "live_plans": live_count,
        "total_plans": len(plans),
        "plans": plans,
    }

    with open("rates.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"✓ rates.json written: {len(plans)} plans ({live_count} live, {len(plans)-live_count} fallback)")
    print(f"  Updated: {output['updated_display']}")
    if failed_areas:
        print(f"  Fallback areas: {', '.join(failed_areas)}")


if __name__ == "__main__":
    build_rates_json()
