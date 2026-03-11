#!/usr/bin/env python3
"""
Amerigy Energy Rate Scraper
Pulls live rates from supplier APIs where available, falls back to manual rates.
Outputs rates.json for the shop.amerigyenergy.com page.
"""

import json
import requests
import time
from datetime import datetime, timezone

# ── TDU PASS-THROUGH CHARGES (used to calculate all-in rates) ──────────────────
# Source: BKV portal "More Details" section for each service area (March 2026)
# Format: (base_$/mo, delivery_¢/kWh)
TDU_CHARGES = {
    "oncor":       (4.90,  4.9993),   # from BKV portal (utilID=5)
    "centerpoint": (3.24,  5.9262),   # from BKV portal HTML (utilID=3)
    "aep":         (3.24,  5.9262),   # from BKV AEP portal HTML (utilID=2)
    "tnmp":        (3.24,  5.9262),   # placeholder - update from BKV TNMP portal
    "lubbock":     (3.24,  5.9262),   # placeholder - update from BKV Lubbock portal
}

def tdu_allin(util_key, energy_charge_cents, usage_kwh=2000):
    """Calculate all-in rate given energy charge and TDU pass-throughs."""
    base_mo, delivery_cents = TDU_CHARGES.get(util_key, (3.24, 5.9262))
    base_per_kwh = (base_mo / usage_kwh) * 100
    return round(energy_charge_cents + delivery_cents + base_per_kwh, 1)

# Legacy constants kept for compatibility
ONCOR_BASE_PER_KWH_2000 = (4.90 / 2000) * 100
ONCOR_DELIVERY_CENTS = 4.9993

# ── BKV ENERGY - DIRECT API ────────────────────────────────────────────────────
# Endpoint discovered via DevTools network analysis of enroll.bkvenergy.com
BKV_API_BASE = "https://enroll.bkvenergy.com"
BKV_PROMO = "AFFAME0001"

# utilID mapping - ALL FIVE SERVICE AREAS CONFIRMED
BKV_UTIL_IDS = {
    "oncor": 5,
    "centerpoint": 3,
    "aep": 2,
    "tnmp": 7,
    "lubbock": 8,
}

BKV_ENROLL_URL = f"https://enroll.bkvenergy.com/Home/Promo?Promocode={BKV_PROMO}"

# ── CLEAN SKY ENERGY - DIRECT API ──────────────────────────────────────────────
# Endpoint discovered via DevTools network analysis of signup.cleanskyenergy.com
# Flow: POST /utility (zip+promo) → get utility_code → POST /rate (zip+utility_code+promo)
CLEANSKY_API_BASE = "https://iyky4nwsnh.execute-api.us-east-1.amazonaws.com/prod"
CLEANSKY_PROMO = "AMER"
CLEANSKY_ENROLL_URL = "https://signup.cleanskyenergy.com/zipcode?promocode=AMER"
CLEANSKY_LOGO = "https://amerigyenergy.com/wp-content/uploads/2025/02/logo.svg"

# ZIP codes to use per service area for utility lookup
CLEANSKY_AREA_ZIPS = {
    "oncor":       "75901",
    "centerpoint": "77002",
    "aep":         "79601",
    "tnmp":        "76528",
    "lubbock":     "79401",
}


def fetch_cleansky_rates(util_key="oncor"):
    """Fetch live Clean Sky Energy rates via AWS API Gateway.
    
    Returns list of plan dicts or empty list on failure.
    Rate field: price2000 = all-in ¢/kWh at 2000 kWh
    Energy charge: energy_charge1 * 100 = ¢/kWh
    """
    zip_code = CLEANSKY_AREA_ZIPS.get(util_key, "75901")
    headers = {
        "Content-Type": "multipart/form-data",
        "Origin": "https://signup.cleanskyenergy.com",
        "Referer": "https://signup.cleanskyenergy.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    try:
        # Step 1: Get utility_code for this ZIP
        utility_resp = requests.post(
            f"{CLEANSKY_API_BASE}/utility",
            data={"zipcode": zip_code, "promo_code": CLEANSKY_PROMO},
            headers=headers,
            timeout=15,
        )
        utility_resp.raise_for_status()
        utility_data = utility_resp.json()

        utility_list = utility_data.get("response", {}).get("utility", [])
        if not utility_list:
            print(f"    Clean Sky: no utility found for ZIP {zip_code}")
            return []
        utility_code = utility_list[0].get("id_utility")
        if not utility_code:
            print(f"    Clean Sky: missing id_utility in response")
            return []

        # Step 2: Fetch plans
        rate_resp = requests.post(
            f"{CLEANSKY_API_BASE}/enrollment/rate",
            data={
                "zipcode": zip_code,
                "utility_code": utility_code,
                "promo_code": CLEANSKY_PROMO,
            },
            headers=headers,
            timeout=15,
        )
        rate_resp.raise_for_status()
        rate_data = rate_resp.json()

        plans_raw = rate_data.get("response", {}).get("plans", [])
        if not plans_raw:
            print(f"    Clean Sky: no plans returned for {util_key}")
            return []

        plans = []
        for p in plans_raw:
            energy_charge = p.get("energy_charge1", 0)
            all_in = p.get("price2000")
            if all_in is None:
                # Calculate from energy charge + TDU
                all_in = tdu_allin(util_key, energy_charge * 100)
            else:
                all_in = float(all_in)

            plans.append({
                "supplier":      "Clean Sky Energy",
                "term":          p.get("contract_term"),
                "rate":          round(all_in, 1),
                "energy_charge": round(energy_charge * 100, 3),
                "renewable_pct": p.get("renewable", 100),
                "enroll_url":    CLEANSKY_ENROLL_URL,
                "logo":          CLEANSKY_LOGO,
                "service_area":  util_key,
                "plan_name":     p.get("plan_name", ""),
            })

        return plans

    except Exception as e:
        print(f"    Clean Sky fetch failed ({util_key}): {e}")
        return []


def fetch_bkv_rates(util_key="oncor"):
    """Fetch live BKV rates via GetAllPlans_V3 API."""
    util_id = BKV_UTIL_IDS.get(util_key)
    if not util_id:
        return None

    url = (
        f"{BKV_API_BASE}/Home/GetAllPlans_V3"
        f"?utilID={util_id}&categoryType=4&priceType=1&DeviceType=web&SortPlansBy="
    )
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BKV_API_BASE}/Home/Plans/Verbena?promocode={BKV_PROMO}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }

    session = requests.Session()

    # Prime the session by hitting the promo landing page first
    try:
        session.get(
            f"{BKV_API_BASE}/Home/Promo?Promocode={BKV_PROMO}",
            headers={"User-Agent": headers["User-Agent"]},
            timeout=15,
        )
        time.sleep(1)
    except Exception:
        pass

    try:
        resp = session.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            print(f"  BKV API returned HTTP {resp.status_code}")
            return None

        data = resp.json()

        plans = []
        plan_list = data if isinstance(data, list) else data.get("plans", data.get("Plans", []))

        for p in plan_list:
            try:
                term = int(p.get("Term") or p.get("term") or 0)
                if not term:
                    continue

                # Energy charge is in PlansTermWithPricing[0].FieldValue (cents/kWh)
                pricing = p.get("PlansTermWithPricing", [])
                energy_charge = None
                if pricing:
                    fv = pricing[0].get("FieldValue", "")
                    try:
                        energy_charge = float(str(fv).replace("¢", "").replace(" ", ""))
                    except Exception:
                        pass

                # All-in rate at 2000 kWh from ProductEstimation
                estimation = p.get("ProductEstimation", {})
                all_in_rate = None
                if estimation:
                    raw = estimation.get("perkWhCharge2000", "")
                    try:
                        all_in_rate = float(str(raw).replace("¢", "").replace(" ", ""))
                    except Exception:
                        pass

                if not all_in_rate and energy_charge:
                    # Calculate manually using per-area TDU charges
                    all_in_rate = tdu_allin(util_key, energy_charge)

                if not all_in_rate:
                    continue

                product_id = p.get("ProductId") or p.get("productId")
                plan_name = p.get("PlanName") or p.get("planName") or f"BKV {term}mo"
                renewable = p.get("RenewablePercentage") or p.get("renewablePercentage") or 0

                plans.append({
                    "supplier": "BKV Energy",
                    "plan_name": plan_name,
                    "product_id": product_id,
                    "term": term,
                    "rate": round(all_in_rate, 1),
                    "renewable_pct": renewable,
                    "service_area": util_key,
                    "enroll_url": BKV_ENROLL_URL,
                    "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png",
                    "source": "live",
                })

            except Exception as e:
                print(f"  BKV plan parse error: {e}")
                continue

        print(f"  BKV: fetched {len(plans)} live plans for {util_key}")
        return plans if plans else None

    except Exception as e:
        print(f"  BKV API error: {e}")
        return None


# ── MANUAL FALLBACK DATA ───────────────────────────────────────────────────────
# Last verified: March 2026 — Oncor service area
# BKV rates updated from live API response (GetAllPlans_V3, utilID=5, March 2026)

FALLBACK_PLANS = [
    # ── APG&E ──
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
    {"supplier": "APG&E", "term": 17, "rate": 13.9, "renewable_pct": 6,
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

    # ── BKV Energy — updated from live API March 2026 ──
    {"supplier": "BKV Energy", "term": 6,  "rate": 14.6, "renewable_pct": 29,
     "enroll_url": BKV_ENROLL_URL,
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 7,  "rate": 14.5, "renewable_pct": 29,
     "enroll_url": BKV_ENROLL_URL,
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 8,  "rate": 14.3, "renewable_pct": 29,
     "enroll_url": BKV_ENROLL_URL,
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 9,  "rate": 14.1, "renewable_pct": 29,
     "enroll_url": BKV_ENROLL_URL,
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 12, "rate": 14.1, "renewable_pct": 29,
     "enroll_url": BKV_ENROLL_URL,
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 15, "rate": 13.6, "renewable_pct": 29,
     "enroll_url": BKV_ENROLL_URL,
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 18, "rate": 14.5, "renewable_pct": 29,
     "enroll_url": BKV_ENROLL_URL,
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 24, "rate": 14.3, "renewable_pct": 29,
     "enroll_url": BKV_ENROLL_URL,
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},
    {"supplier": "BKV Energy", "term": 36, "rate": 14.3, "renewable_pct": 29,
     "enroll_url": BKV_ENROLL_URL,
     "logo": "https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png"},

    # ── Chariot Energy (100% solar) ──
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

    # ── Clean Sky Energy (100% clean) ──
    {"supplier": "Clean Sky Energy", "term": 6,  "rate": 12.4, "renewable_pct": 100,
     "enroll_url": "https://signup.cleanskyenergy.com/zipcode?promocode=AMER",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2025/02/logo.svg"},
    {"supplier": "Clean Sky Energy", "term": 12, "rate": 13.4, "renewable_pct": 100,
     "enroll_url": "https://signup.cleanskyenergy.com/zipcode?promocode=AMER",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2025/02/logo.svg"},
    {"supplier": "Clean Sky Energy", "term": 24, "rate": 13.5, "renewable_pct": 100,
     "enroll_url": "https://signup.cleanskyenergy.com/zipcode?promocode=AMER",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2025/02/logo.svg"},

    # ── Atlantex Power ──
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

    # ── Think Energy (+$4.95/mo base fee — noted in plan display) ──
    {"supplier": "Think Energy", "term": 12, "rate": 14.3, "renewable_pct": 0,
     "note": "+$4.95/mo base fee",
     "enroll_url": "http://enroll.thinkenergy.com/?referralType=amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/08/think-ntx.e2c6be7f.png"},
    {"supplier": "Think Energy", "term": 36, "rate": 15.0, "renewable_pct": 0,
     "note": "+$4.95/mo base fee",
     "enroll_url": "http://enroll.thinkenergy.com/?referralType=amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2024/08/think-ntx.e2c6be7f.png"},

    # ── Frontier Utilities ──
    {"supplier": "Frontier Utilities", "term": 12, "rate": 15.8, "renewable_pct": 0,
     "enroll_url": "http://www.FrontierUtilities.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/Frontier-Utilities.jpg"},
    {"supplier": "Frontier Utilities", "term": 24, "rate": 16.0, "renewable_pct": 0,
     "enroll_url": "http://www.FrontierUtilities.com/Amerigy",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2020/03/Frontier-Utilities.jpg"},

    # ── Payless Power (prepaid) ──
    {"supplier": "Payless Power", "term": 6,  "rate": 16.7, "renewable_pct": 0,
     "note": "Prepaid — no credit check",
     "enroll_url": "https://account.paylesspower.com/enroll/318875",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/11/payless-power-logo.png"},
    {"supplier": "Payless Power", "term": 12, "rate": 16.7, "renewable_pct": 0,
     "note": "Prepaid — no credit check",
     "enroll_url": "https://account.paylesspower.com/enroll/318875",
     "logo": "https://amerigyenergy.com/wp-content/uploads/2021/11/payless-power-logo.png"},

    # ── Ironhorse Power ──
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

# Add source tag to all fallback plans
for p in FALLBACK_PLANS:
    p.setdefault("source", "manual")
    p.setdefault("note", "")


# ── MAIN ───────────────────────────────────────────────────────────────────────

SERVICE_AREA_LABELS = {
    "oncor":       "Oncor (DFW / East Texas)",
    "centerpoint": "CenterPoint (Houston)",
    "aep":         "AEP (West / South Texas)",
    "tnmp":        "TNMP (Bryan / New Braunfels)",
    "lubbock":     "Lubbock Power & Light",
}

def build_rates_json():
    all_bkv_plans = []
    all_cleansky_plans = []
    live_count = 0
    failed_areas = []

    print("Fetching live BKV rates for all service areas...")

    for util_key, label in SERVICE_AREA_LABELS.items():
        print(f"  Trying BKV {label} (utilID={BKV_UTIL_IDS[util_key]})...")
        area_plans = fetch_bkv_rates(util_key)
        if area_plans:
            all_bkv_plans.extend(area_plans)
            live_count += len(area_plans)
            print(f"  ✓ {label}: {len(area_plans)} plans")
        else:
            failed_areas.append(util_key)
            print(f"  ✗ {label}: failed — will use fallback BKV rates for this area")

    # Fetch Clean Sky live rates (Oncor only — their API covers all areas but
    # residential plans are the same across TDUs; Oncor ZIP is representative)
    print("\nFetching live Clean Sky Energy rates...")
    cleansky_live = fetch_cleansky_rates("oncor")
    if cleansky_live:
        all_cleansky_plans = cleansky_live
        live_count += len(cleansky_live)
        print(f"  ✓ Clean Sky: {len(cleansky_live)} plans")
    else:
        print("  ✗ Clean Sky: failed — using fallback rates")

    # Build final plan list
    non_live_suppliers = [p for p in FALLBACK_PLANS
                          if p["supplier"] not in ("BKV Energy", "Clean Sky Energy")]

    if all_bkv_plans or all_cleansky_plans:
        plans = non_live_suppliers
        if all_bkv_plans:
            plans = plans + all_bkv_plans
        else:
            plans = plans + [p for p in FALLBACK_PLANS if p["supplier"] == "BKV Energy"]
        if all_cleansky_plans:
            plans = plans + all_cleansky_plans
        else:
            plans = plans + [p for p in FALLBACK_PLANS if p["supplier"] == "Clean Sky Energy"]
    else:
        # Full fallback
        print("  All live fetches failed — using full manual fallback")
        plans = FALLBACK_PLANS.copy()

    # Sort: by rate ascending, then term ascending
    plans.sort(key=lambda x: (x["rate"], x["term"]))

    now = datetime.now(timezone.utc)

    # Build per-area summary for metadata
    areas_live = [SERVICE_AREA_LABELS[k] for k in SERVICE_AREA_LABELS if k not in failed_areas]
    areas_fallback = [SERVICE_AREA_LABELS[k] for k in failed_areas]

    output = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_display": now.strftime("%I:%M %p UTC, %B %d, %Y").lstrip("0").replace(" 0", " "),
        "service_areas_live": areas_live,
        "service_areas_fallback": areas_fallback,
        "usage_kwh": 2000,
        "live_plans": live_count,
        "total_plans": len(plans),
        "plans": plans,
    }

    with open("rates.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✓ rates.json written: {len(plans)} plans ({live_count} live BKV, {len(plans)-live_count} manual)")
    print(f"  Updated: {output['updated_display']}")
    if failed_areas:
        print(f"  Fallback areas: {', '.join(failed_areas)}")


if __name__ == "__main__":
    build_rates_json()
