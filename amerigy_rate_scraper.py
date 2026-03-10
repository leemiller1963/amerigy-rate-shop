#!/usr/bin/env python3
"""
amerigy_rate_scraper.py
=======================
Pulls live Amerigy supplier rates from the official PUCT
Power to Choose API (powertochoose.org).

No browser automation needed — the API returns structured JSON.
Runs via GitHub Actions and injects rates into index.html.

SETUP:
  pip install requests

USAGE:
  python amerigy_rate_scraper.py              # fetch all areas
  python amerigy_rate_scraper.py --area oncor  # one area only
  python amerigy_rate_scraper.py --dry-run     # print, don't write
  python amerigy_rate_scraper.py --fallback-only  # use manual rates only
"""

import json, time, argparse, logging, re, requests
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('amerigy-scraper')
CT = ZoneInfo("America/Chicago")

# ─── SERVICE AREAS ────────────────────────────────────────────────────────────
AREA_ZIPS = {
    "oncor":        "75901",
    "centerpoint":  "77002",
    "aep":          "79601",
    "tnmp":         "77803",
    "lubbock":      "79401",
}
AREA_LABELS = {
    "oncor":        "Oncor",
    "centerpoint":  "CenterPoint (Houston)",
    "aep":          "AEP Texas",
    "tnmp":         "TNMP",
    "lubbock":      "Lubbock Power & Light",
}

# ─── SUPPLIER CONFIG ──────────────────────────────────────────────────────────
SUPPLIER_MAP = {
    "bkv":      {"name":"BKV Energy",        "logo":"https://amerigyenergy.com/wp-content/uploads/2023/09/BKV_Logo_Vertical_RGB.png",              "enrollUrl":"https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",                                "match":["bkv energy","bkv"]},
    "atlantex": {"name":"Atlantex Power",     "logo":"https://amerigyenergy.com/wp-content/uploads/2024/10/ae-texas-temp-logo.png",                "enrollUrl":"https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",                  "match":["atlantex"]},
    "think":    {"name":"Think Energy",       "logo":"https://amerigyenergy.com/wp-content/uploads/2024/08/think-ntx.e2c6be7f.png",                "enrollUrl":"http://enroll.thinkenergy.com/?referralType=amerigy",   "baseFee":4.95,                          "match":["think energy"]},
    "ironhorse":{"name":"Ironhorse Power",    "logo":"https://amerigyenergy.com/wp-content/uploads/2024/10/Ironhorse.svg",                         "enrollUrl":"https://signup.ironhorsepowerservices.com/Amerigy",                                           "match":["ironhorse"]},
    "cleansky": {"name":"Clean Sky Energy",   "logo":"https://amerigyenergy.com/wp-content/uploads/2025/02/logo.svg",                              "enrollUrl":"https://signup.cleanskyenergy.com/zipcode?promocode=AMER",                                   "match":["clean sky"]},
    "chariot":  {"name":"Chariot Energy",     "logo":"https://amerigyenergy.com/wp-content/uploads/2020/03/chariot.png",                           "enrollUrl":"https://signup.chariotenergy.com/Home/?Promocode=AMERIGY050",                               "match":["chariot"]},
    "apge":     {"name":"APG&E",              "logo":"https://amerigyenergy.com/wp-content/uploads/2021/01/APGE2_result-e1611274352488.jpg",        "enrollUrl":"https://www.apge.com/amerigy",                                                                "match":["apg&e","apge","ap gas"]},
    "payless":  {"name":"Payless Power",      "logo":"https://amerigyenergy.com/wp-content/uploads/2021/11/payless-power-logo.png",                "enrollUrl":"https://account.paylesspower.com/enroll/318875",                                              "match":["payless"]},
    "frontier": {"name":"Frontier Utilities", "logo":"https://amerigyenergy.com/wp-content/uploads/2020/03/Frontier-Utilities.jpg",                "enrollUrl":"http://www.FrontierUtilities.com/Amerigy",                                                    "match":["frontier utilities","frontier"]},
}
MATCH_LOOKUP = {m: sid for sid, cfg in SUPPLIER_MAP.items() for m in cfg["match"]}

def match_supplier(company_name):
    n = company_name.lower().strip()
    for m, sid in MATCH_LOOKUP.items():
        if m in n:
            return sid
    return None

# ─── POWER TO CHOOSE API ──────────────────────────────────────────────────────
P2C_BASE = "https://www.powertochoose.org/en-us/Plan/Results"
P2C_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AmerigyRateFetcher/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.powertochoose.org/",
}

def fetch_p2c_plans(zip_code, page_size=200):
    plans, page = [], 1
    while True:
        try:
            r = requests.get(P2C_BASE, params={"zip": zip_code, "page": page, "pageSize": page_size},
                             headers=P2C_HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.error(f"P2C fetch failed ZIP {zip_code} page {page}: {e}")
            break
        batch = data if isinstance(data, list) else data.get("plans", data.get("Plans", []))
        if not batch:
            break
        plans.extend(batch)
        log.info(f"  ZIP {zip_code} page {page}: {len(batch)} plans")
        if len(batch) < page_size:
            break
        page += 1
        time.sleep(0.5)
    return plans

def parse_renewable(desc):
    if not desc: return 0
    m = re.search(r'(\d+)\s*%', str(desc))
    return int(m.group(1)) if m else 0

def parse_term(val):
    if not val: return 12
    try: return int(str(val).replace('months','').strip())
    except: return 12

def p2c_to_plan(p2c, area, sid):
    cfg = SUPPLIER_MAP[sid]
    rate = float(p2c.get("Price", p2c.get("price", p2c.get("EnergyCharge", 0))) or 0)
    if not (5.0 <= rate <= 30.0):
        return None
    term = parse_term(p2c.get("Term", p2c.get("ContractTermMonths")))
    base_fee = float(p2c.get("BaseChargeAmount", p2c.get("baseChargeAmount", cfg.get("baseFee", 0))) or 0)
    renewable = parse_renewable(p2c.get("RenewableEnergyDescription", p2c.get("renewableEnergyDescription", "")))
    tags = ["Fixed Rate"]
    if renewable >= 100: tags.append("100% Renewable")
    elif renewable > 0:  tags.append(f"{renewable}% Renewable")
    if base_fee == 0:    tags.append("No Base Fee")
    if term <= 6:        tags.append("Short Term")
    return {
        "supplier": cfg["name"], "logo": cfg["logo"],
        "area": area, "areaLabel": AREA_LABELS[area],
        "rateKwh": round(rate, 2), "termMonths": term,
        "baseFee": round(base_fee, 2), "renewable": renewable,
        "enrollUrl": cfg["enrollUrl"],
        "planName": p2c.get("RateCode", p2c.get("PlanName", "")),
        "tags": tags, "bestValue": False, "source": "powertochoose",
    }

# ─── FALLBACK (manually verified Oncor rates, March 2026) ────────────────────
def _fb(supplier, logo, area, rate, term, base_fee, renewable, enroll, tags):
    return {"supplier":supplier,"logo":logo,"area":area,"areaLabel":AREA_LABELS[area],
            "rateKwh":rate,"termMonths":term,"baseFee":base_fee,"renewable":renewable,
            "enrollUrl":enroll,"planName":"","tags":tags,"bestValue":False,"source":"manual"}

_L  = "https://amerigyenergy.com/wp-content/uploads/"
FALLBACK_PLANS = [
    # APG&E
    *[_fb("APG&E",f"{_L}2021/01/APGE2_result-e1611274352488.jpg","oncor",r,t,0,6,"https://www.apge.com/amerigy",["Fixed Rate"]+(['Short Term'] if t<=6 else []))
      for r,t in [(11.3,3),(14.5,6),(14.0,12),(13.8,15),(13.9,17),(14.4,18),(14.3,24),(14.5,36)]],
    # BKV
    *[_fb("BKV Energy",f"{_L}2023/09/BKV_Logo_Vertical_RGB.png","oncor",r,t,0,0,"https://enroll.bkvenergy.com/Home/Promo?Promocode=AFFAME0001",["Fixed Rate"]+(['Short Term'] if t<=6 else []))
      for r,t in [(14.7,6),(14.6,7),(14.4,8),(14.3,9),(14.3,12),(13.9,15),(14.6,18),(14.6,24),(14.4,36)]],
    # Chariot
    *[_fb("Chariot Energy",f"{_L}2020/03/chariot.png","oncor",r,t,0,100,"https://signup.chariotenergy.com/Home/?Promocode=AMERIGY050",["Fixed Rate","100% Renewable"])
      for r,t in [(13.8,12),(14.9,15),(14.8,18),(13.9,24),(14.2,36)]],
    # Clean Sky
    *[_fb("Clean Sky Energy",f"{_L}2025/02/logo.svg","oncor",r,t,0,100,"https://signup.cleanskyenergy.com/zipcode?promocode=AMER",["Fixed Rate","100% Renewable"]+(['Short Term'] if t<=6 else []))
      for r,t in [(12.4,6),(13.4,12),(13.5,24)]],
    # Atlantex
    *[_fb("Atlantex Power",f"{_L}2024/10/ae-texas-temp-logo.png","oncor",r,t,0,0,"https://enroll.atlantexpower.com/Enrollment/Default.aspx?promoCode=AMERIGY",["Fixed Rate"])
      for r,t in [(14.9,12),(13.8,15),(14.5,24),(14.8,36)]],
    # Think Energy
    *[_fb("Think Energy",f"{_L}2024/08/think-ntx.e2c6be7f.png","oncor",r,t,4.95,0,"http://enroll.thinkenergy.com/?referralType=amerigy",["Fixed Rate"])
      for r,t in [(14.3,12),(15.0,36)]],
    # Frontier
    *[_fb("Frontier Utilities",f"{_L}2020/03/Frontier-Utilities.jpg","oncor",r,t,0,0,"http://www.FrontierUtilities.com/Amerigy",["Fixed Rate"])
      for r,t in [(15.8,12),(16.0,24)]],
    # Payless
    *[_fb("Payless Power",f"{_L}2021/11/payless-power-logo.png","oncor",r,t,0,0,"https://account.paylesspower.com/enroll/318875",["Fixed Rate","Prepaid"]+(['Short Term'] if t<=6 else []))
      for r,t in [(16.7,6),(16.7,12)]],
    # Ironhorse
    *[_fb("Ironhorse Power",f"{_L}2024/10/Ironhorse.svg","oncor",r,t,0,0,"https://signup.ironhorsepowerservices.com/Amerigy",["Fixed Rate"]+(['Short Term'] if t<=6 else []))
      for r,t in [(13.0,3),(15.7,6),(15.1,9),(15.1,12),(14.8,15),(15.5,24),(15.7,36)]],
]

# ─── CORE LOGIC ───────────────────────────────────────────────────────────────
def fetch_area(area, zip_code):
    raw = fetch_p2c_plans(zip_code)
    log.info(f"  {len(raw)} total plans from P2C for {area}")
    matched = []
    for p in raw:
        company = p.get("CompanyName", p.get("companyName", p.get("ProviderName", "")))
        sid = match_supplier(company)
        if not sid: continue
        plan = p2c_to_plan(p, area, sid)
        if plan: matched.append(plan)
    log.info(f"  Matched {len(matched)} Amerigy plans")
    return matched

def merge_fallback(live_plans):
    live_keys = {(p["supplier"], p["area"]) for p in live_plans}
    result = list(live_plans)
    for fp in FALLBACK_PLANS:
        if (fp["supplier"], fp["area"]) not in live_keys:
            result.append(fp)
    return result

def mark_best_values(plans):
    by_area = {}
    for p in plans:
        a = p["area"]
        if a not in by_area or p["rateKwh"] < by_area[a]["rateKwh"]:
            by_area[a] = p
    for p in plans:
        p["bestValue"] = (by_area.get(p["area"]) is p)
    return plans

def build_output(plans, errors):
    now = datetime.now(CT)
    nh = 19 if now.hour < 19 else 7
    nxt = now.replace(hour=nh, minute=0, second=0, microsecond=0)
    if nh == 7 and now.hour >= 19:
        nxt += timedelta(days=1)
    return {"lastUpdated": now.isoformat(), "nextUpdate": nxt.isoformat(),
            "errors": errors, "plans": plans}

def inject_into_html(rate_data, path):
    html = path.read_text()
    js = json.dumps(rate_data, indent=2, default=str)
    new = re.sub(r'(const RATE_DATA\s*=\s*)(\{.*?\});', rf'\g<1>{js};', html, flags=re.DOTALL)
    path.write_text(new)
    log.info(f"Injected rates → {path}")

# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--area")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fallback-only", action="store_true")
    args = parser.parse_args()

    areas  = [args.area] if args.area else list(AREA_ZIPS.keys())
    plans, errors = [], []

    if args.fallback_only:
        log.info("Using manual fallback rates only")
        plans = list(FALLBACK_PLANS)
    else:
        for area in areas:
            try:
                plans.extend(fetch_area(area, AREA_ZIPS[area]))
            except Exception as e:
                log.error(f"Failed {area}: {e}")
                errors.append({"area": area, "error": str(e)})
        plans = merge_fallback(plans)

    plans  = mark_best_values(plans)
    output = build_output(plans, errors)

    live   = sum(1 for p in plans if p.get("source") == "powertochoose")
    manual = sum(1 for p in plans if p.get("source") == "manual")
    log.info(f"Total {len(plans)} plans — {live} live from P2C, {manual} from manual fallback")

    if args.dry_run:
        print(json.dumps(output, indent=2, default=str))
        return

    Path("rates.json").write_text(json.dumps(output, indent=2, default=str))
    log.info("Wrote rates.json")

    for name in ["index.html", "amerigy-shop.html"]:
        p = Path(name)
        if p.exists():
            inject_into_html(output, p)
            break

if __name__ == "__main__":
    main()
