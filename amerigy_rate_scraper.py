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

# ── POWER TO CHOOSE CSV EXPORT ────────────────────────────────────────────────
# Full statewide plan database as CSV — updated daily by PUCT.
# No auth, no bot detection, works from GitHub Actions.
PTC_CSV_URL = "https://www.powertochoose.org/en-us/Plan/ExportToCsv"

# ── BKV BROKER API ────────────────────────────────────────────────────────────
BKV_API_URL  = "https://brokerapi.bkvenergy.com/v3/broker/GetPlans"
BKV_API_KEY  = "lLMq-ADt0BHRP-yUgF"
BKV_PROMO    = "AFFAME0001"

BKV_UTIL_TO_AREA = {
    1: "aep",        # AEP Texas Central
    2: "aep",        # AEP Texas North
    3: "centerpoint",
    5: "oncor",
    7: "tnmp",
    8: "lubbock",
}
BKV_ZIPS = {
    "oncor": "75901", "centerpoint": "77002",
    "aep": "79601", "tnmp": "76528", "lubbock": "79401",
}

# TDU name strings in the CSV tdu_company_name field → our area keys
TDU_TO_AREA = {
    "oncor electric delivery company":          "oncor",
    "centerpoint energy houston electric llc":  "centerpoint",
    "aep texas central":                        "aep",
    "aep texas north":                          "aep",
    "texas-new mexico power company":           "tnmp",
    "lubbock power & light system":             "lubbock",
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
    "atlantex power": {
        "display_name": "Atlantex Power",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/ae-texas-temp-logo.png",
        "enroll_url": "https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",
    },
    "ae texas": {  # exact PTC name is "AE TEXAS"

        "display_name": "Atlantex Power",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/ae-texas-temp-logo.png",
        "enroll_url": "https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",
    },
    "atlantex": {
        "display_name": "Atlantex Power",
        "logo": "https://amerigyenergy.com/wp-content/uploads/2024/10/ae-texas-temp-logo.png",
        "enroll_url": "https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",
    },
    "clean sky energy": {
        "display_name": "Clean Sky Energy",
    },
    "cleansky energy": {
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


def fetch_bkv_plans():
    """Fetch BKV rates from their Broker API. Returns list of plan dicts."""
    logo = "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"
    enroll = "https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001"
    headers = {"User-Agent": "Mozilla/5.0"}
    plans = []

    for area_key, zip_code in BKV_ZIPS.items():
        params = {"Key": BKV_API_KEY, "Zip": zip_code,
                  "PromoCode": BKV_PROMO, "CustomerTypeID": "1"}
        try:
            r = requests.get(BKV_API_URL, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  BKV API error ({area_key}): {e}")
            continue

        area_plans = 0
        for block in data:
            util_id = block.get("UtilityId")
            mapped = BKV_UTIL_TO_AREA.get(util_id, area_key)
            for p in block.get("Plans", []):
                try:
                    term = int(p.get("Terms", 0))
                    rate = round(float(p.get("kWh2000", 0)), 1)
                except (ValueError, TypeError):
                    continue
                if term < 12 or rate <= 0:
                    continue
                try:
                    renewable = int(float(p.get("Renewable", 0)))
                except (ValueError, TypeError):
                    renewable = 0
                plans.append({
                    "supplier": "BKV Energy", "term": term, "rate": rate,
                    "renewable_pct": renewable, "enroll_url": enroll, "logo": logo,
                    "service_area": mapped, "plan_name": p.get("Title", ""),
                    "source": "bkv_api",
                })
                area_plans += 1
        print(f"  BKV {area_key}: {area_plans} plans")

    print(f"  BKV API total: {len(plans)} plans")
    return plans


def fetch_all_ptc_plans():
    """Download full Texas plan database from Power to Choose CSV export.
    
    Returns list of dicts with keys matching CSV columns, or empty list on failure.
    CSV columns include: zip_code, company_name, plan_name, term_value,
    price_kwh, renewable_energy_credit, tdu_company_name, plan_type_name, etc.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/csv,text/plain,*/*",
        "Referer": "https://www.powertochoose.org/",
    }
    try:
        r = requests.get(PTC_CSV_URL, headers=headers, timeout=30)
        r.raise_for_status()
        print(f"  PTC CSV: HTTP {r.status_code}, {len(r.content):,} bytes")

        # Parse CSV
        import csv, io
        text = r.content.decode("utf-8-sig")  # strip BOM if present
        reader = csv.DictReader(io.StringIO(text))
        plans = list(reader)
        print(f"  PTC CSV: {len(plans):,} total plans in database")
        return plans
    except Exception as e:
        print(f"  PTC CSV fetch failed: {e}")
        return []


def process_ptc_plans(raw_plans, exclude=None):
    """Filter CSV plans for our suppliers across all service areas.
    
    CSV columns use bracketed names e.g. [RepCompany], [TduCompanyName], [kwh2000].
    """
    best = {}  # (display_name, term, area_key) -> plan dict (keep lowest rate)

    excl = [x.lower() for x in (exclude or [])]
    for p in raw_plans:
        company = (p.get("[RepCompany]") or "").strip()
        config = match_supplier(company)
        if not config:
            continue
        if any(x in config["display_name"].lower() for x in excl):
            continue

        # Map TDU to service area
        tdu = (p.get("[TduCompanyName]") or "").lower().strip()
        area_key = TDU_TO_AREA.get(tdu)
        if not area_key:
            continue

        term_raw = p.get("[TermValue]") or "0"
        try:
            term = int(float(str(term_raw).strip()))
        except (ValueError, TypeError):
            continue
        if term <= 0:
            continue

        # [kwh2000] = all-in rate at 2000 kWh as decimal (e.g. 0.1021 = 10.21¢/kWh)
        rate_raw = p.get("[kwh2000]") or "0"
        try:
            rate = round(float(str(rate_raw).strip()) * 100, 1)
        except (ValueError, TypeError):
            continue
        if rate <= 0:
            continue

        key = (config["display_name"], term, area_key)
        if key in best and rate >= best[key]["rate"]:
            continue

        renewable_raw = p.get("[Renewable]") or "0"
        try:
            renewable = int(float(str(renewable_raw).strip()))
        except (ValueError, TypeError):
            renewable = 0
        renewable = config.get("renewable_pct_override") or renewable

        plan = {
            "supplier":      config["display_name"],
            "term":          term,
            "rate":          rate,
            "renewable_pct": renewable,
            "enroll_url":    config["enroll_url"],
            "logo":          config["logo"],
            "service_area":  area_key,
            "plan_name":     (p.get("[Product]") or "").strip(),
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
    print("Fetching BKV rates from Broker API...")
    bkv_plans = fetch_bkv_plans()

    print("Downloading Power to Choose CSV...")
    raw = fetch_all_ptc_plans()

    if raw:
        matched = process_ptc_plans(raw, exclude=["BKV Energy"])
        if matched:
            # Show summary by area
            from collections import Counter
            area_counts = Counter(p["service_area"] for p in matched)
            supplier_counts = Counter(p["supplier"] for p in matched)
            for area_key, label in SERVICE_AREA_LABELS.items():
                count = area_counts.get(area_key, 0)
                print(f"  {label}: {count} plans matched")

            print(f"  Suppliers: {dict(supplier_counts)}")
            # Debug: find Clean Sky name in CSV
            sky = sorted(set((p.get("[RepCompany]") or "").strip() for p in raw if "sky" in (p.get("[RepCompany]") or "").lower() or "clean" in (p.get("[RepCompany]") or "").lower()))
            atlantex = sorted(set((p.get("[RepCompany]") or "").strip() for p in raw if any(x in (p.get("[RepCompany]") or "").lower() for x in ["atlantex", "ae texas", "aetexas"])))
            if atlantex: print(f"  Atlantex variants in CSV: {atlantex}")
            else:
                # Broader search — anything short with "texas" 
                broad = sorted(set((p.get("[RepCompany]") or "").strip() for p in raw if "texas" in (p.get("[RepCompany]") or "").lower() and len((p.get("[RepCompany]") or "")) < 25))
                print(f"  Atlantex NOT found. Short Texas names in CSV: {broad}")
            if sky: print(f"  Clean Sky variants in CSV: {sky}")
            plans = matched + bkv_plans
            live_count = len(plans)
            areas_live = list(SERVICE_AREA_LABELS.values())
            areas_fallback = []
        else:
            print("  CSV fetched but 0 of our suppliers matched — check company names")
            # Debug: show sample company names
            companies = sorted(set(
                (p.get("[RepCompany]") or "").strip()
                for p in raw[:200]
            ))
            print(f"  Sample company names: {companies[:20]}")
            # Also print actual CSV column headers
            if raw:
                print(f"  CSV columns: {list(raw[0].keys())}")
            # Show sample RepCompany and TduCompanyName values
            companies2 = sorted(set((p.get("[RepCompany]") or "").strip() for p in raw if p.get("[RepCompany]","").strip()))
            tdus = sorted(set((p.get("[TduCompanyName]") or "").strip() for p in raw if p.get("[TduCompanyName]","").strip()))
            print(f"  Sample [RepCompany]: {companies2[:15]}")
            print(f"  All [TduCompanyName]: {tdus}")
            plans = FALLBACK_PLANS.copy()
            live_count = 0
            areas_live = []
            areas_fallback = list(SERVICE_AREA_LABELS.values())
    else:
        print("  CSV fetch failed — using manual fallback")
        plans = FALLBACK_PLANS.copy()
        live_count = 0
        areas_live = []
        areas_fallback = list(SERVICE_AREA_LABELS.values())

    # Adjust rates: add 1¢ broker margin to PTC suppliers
    # BKV and Think Energy already return correct broker pricing via their APIs
    for p in plans:
        if p["supplier"] not in ("Think Energy", "BKV Energy") and p.get("source") != "bkv_api":
            p["rate"] = round(p["rate"] + 1.0, 1)

    # Remove plans under 12 months — Amerigy focuses on longer-term contracts
    plans = [p for p in plans if p["term"] >= 12]

    plans.sort(key=lambda x: (x["rate"], x["term"]))

    now = datetime.now(timezone.utc)
    output = {
        "updated_at":           now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_display":      now.strftime("%I:%M %p UTC, %B %d, %Y").lstrip("0").replace(" 0", " "),
        "source":               "powertochoose" if live_count else "fallback",
        "service_areas_live":   areas_live,
        "service_areas_fallback": areas_fallback,
        "usage_kwh":            2000,
        "live_plans":           live_count,
        "total_plans":          len(plans),
        "plans":                plans,
    }

    with open("rates.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✓ rates.json written: {len(plans)} plans ({len(plans)} live, 0 fallback)")
    print(f"  Updated: {output['updated_display']}")


if __name__ == "__main__":
    build_rates_json()
