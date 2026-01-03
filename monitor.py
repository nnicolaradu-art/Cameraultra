import json
import os
import re
import time
from typing import List, Dict, Set
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode

EBAY_BASE = "https://www.ebay.co.uk/sch/i.html"

# CATEGORII:
# 625 = Cameras & Photography
# 45089 = Camera Mixed Lots
CATEGORIES = [625, 45089]

# Buy It Now only (licitații OFF)
BUY_IT_NOW_ONLY = True

# Filtre eBay:
# - LH_Lots=1 => listed as lots
# - LH_PrefLoc=2 => worldwide
# - LH_AvailTo=3 => available to UK
BASE_PARAMS = {
    "LH_Lots": "1",
    "LH_PrefLoc": "2",
    "LH_AvailTo": "3",
}

# Blocăm doar junk sigur (NU blocăm untested/job lot/mixed/collection/read description)
BLOCKLIST = [
    "junk",
    "spares",
    "broken",
    "repair",
    "parts only",
    "accessories only",
    "camera case lot",
    "camera cases lot",
    "camera bag lot",
    "camera bags lot",
]

# Branduri (folosite în scoring)
BRANDS = [
    "nikon", "canon", "olympus", "pentax", "konica", "minolta",
    "sony", "panasonic", "fujifilm", "ricoh", "casio", "kodak",
    "polaroid", "leica", "hasselblad", "mamiya", "contax", "yashica",
    "zenit", "praktica", "chinon", "rollei", "agfa"
]

# Semnale “UK clearance”
UK_HINTS = [
    "job lot", "joblot", "bundle", "collection", "mixed lot", "mixed",
    "house clearance", "loft find", "garage find", "estate", "charity",
    "vintage", "old", "retro"
]

# Semnale lot mare
BIG_LOT_HINTS = [
    "huge lot", "massive lot", "large lot", "big lot", "bulk",
    "box of", "crate of", "bag of", "bundle of"
]

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "hundreds": 100
}

SEEN_PATH = "seen.json"
UA = "Mozilla/5.0 (compatible; CameraLotUltra/UK/1.1)"

# =========================
# TELEGRAM
# =========================
def tg_send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram secrets missing")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=20)
    if r.status_code != 200:
        print("Telegram error:", r.status_code, r.text)

# =========================
# SEEN CACHE
# =========================
def load_seen() -> Set[str]:
    if not os.path.exists(SEEN_PATH):
        return set()
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f).get("seen_ids", []))
    except Exception:
        return set()

def save_seen(seen: Set[str]) -> None:
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump({"seen_ids": list(seen)[-3000:]}, f)

# =========================
# HELPERS
# =========================
def hard_reject(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in BLOCKLIST)

def extract_qty(title: str):
    t = title.lower()

    # "127 cameras"
    m = re.search(r"\b(\d{1,4})\s*(?:x\s*)?(?:camera|cameras|camcorder|camcorders)\b", t)
    if m:
        try:
            return int(m.group(1))
        except:
            pass

    # "lot of 70"
    m = re.search(r"\b(?:lot|job lot|joblot|bundle|box|crate|bag)\s+of\s+(\d{1,4})\b", t)
    if m:
        try:
            return int(m.group(1))
        except:
            pass

    # "seventy cameras"
    for w, val in WORD_NUMBERS.items():
        if re.search(rf"\b{re.escape(w)}\s+(?:camera|cameras|camcorder|camcorders)\b", t):
            return val

    # "one hundred cameras"
    if re.search(r"\bone\s+hundred\s+(?:camera|cameras|camcorder|camcorders)\b", t):
        return 100

    return None

def score_listing(title: str) -> int:
    t = title.lower()
    if hard_reject(t):
        return -999

    score = 0

    # tipuri
    if re.search(r"\b(camera|cameras|compact|digicam|dslr|slr|tlr|rangefinder|film|35mm|instant|camcorder)\b", t):
        score += 2

    # branduri
    if any(b in t for b in BRANDS):
        score += 2

    # hints
    for w in UK_HINTS:
        if w in t:
            score += 1

    for w in BIG_LOT_HINTS:
        if w in t:
            score += 2

    # cantitate
    qty = extract_qty(title)
    if qty is not None:
        score += 2
        if qty >= 20: score += 3
        if qty >= 50: score += 4
        if qty >= 100: score += 5

    # working hints
    if re.search(r"\b(shutter working|shutters working|tested working|fully working)\b", t):
        score += 2

    return score

def build_url(term: str, category: int) -> str:
    params = dict(BASE_PARAMS)
    params["_nkw"] = term
    params["_sacat"] = str(category)
    if BUY_IT_NOW_ONLY:
        params["LH_BIN"] = "1"
    return f"{EBAY_BASE}?{urlencode(params)}"

def fetch_search(url: str) -> List[Dict]:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=35)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    items = []
    for li in soup.select("li.s-item"):
        a = li.select_one("a.s-item__link")
        title_el = li.select_one(".s-item__title")
        price_el = li.select_one(".s-item__price")
        if not a or not title_el or not price_el:
            continue

        title = title_el.get_text(" ", strip=True)
        if len(title) < 6 or title.lower() in ("shop on ebay", "sponsored"):
            continue

        link = a.get("href", "").split("?")[0]
        price = price_el.get_text(" ", strip=True)
        items.append({"id": link, "title": title, "price": price, "link": link})

    return items

# =========================
# ULTRA KEYWORDS (rotație pe grupuri)
# =========================
SEARCH_GROUPS = [
    # G0: generic lots (UK wording)
    [
        "camera job lot", "cameras job lot", "joblot cameras", "job lot cameras",
        "camera lot", "cameras lot", "camera bundle", "camera bundles",
        "camera collection", "camera collections", "mixed camera lot", "mixed cameras",
        "house clearance cameras", "loft find cameras", "garage find cameras",
        "vintage camera lot", "old camera lot", "retro camera lot"
    ],

    # G1: digital/compact/digicam (Etsy-friendly)
    [
        "digital camera lot", "digital cameras lot", "compact camera lot", "compact cameras lot",
        "digicam lot", "digicams lot", "point and shoot lot", "point and shoot camera lot",
        "pocket camera lot", "small camera lot", "bridge camera lot", "zoom camera lot",
        "early digital camera lot", "2000s digital camera lot", "vintage digital camera lot"
    ],

    # G2: film/vintage/35mm + odd formats
    [
        "film camera lot", "film cameras lot", "35mm camera lot", "35mm cameras lot",
        "slr film camera lot", "rangefinder camera lot", "tlr camera lot",
        "medium format camera lot", "6x6 camera lot", "instant camera lot", "polaroid camera lot",
        "vintage film camera lot", "old film cameras lot"
    ],

    # G3: DSLR/SLR digital (loturi mici bune)
    [
        "dslr camera lot", "dslr cameras lot", "slr camera lot", "slr cameras lot",
        "digital slr lot", "camera bodies lot", "camera body lot",
        "nikon dslr lot", "canon dslr lot", "pentax dslr lot"
    ],

    # G4: brand-heavy (curat, dar variat)
    [
        "nikon camera lot", "canon camera lot", "olympus camera lot", "pentax camera lot",
        "konica camera lot", "minolta camera lot", "fujifilm camera lot", "sony camera lot",
        "panasonic camera lot", "ricoh camera lot", "kodak camera lot", "casio camera lot",
        "polaroid lot cameras", "leica camera lot"
    ],

    # G5: digicam families (foarte eficiente)
    [
        "canon powershot lot", "nikon coolpix lot", "sony cybershot lot",
        "fujifilm finepix lot", "panasonic lumix lot", "olympus stylus lot", "olympus mju lot",
        "ricoh caplio lot", "kodak easyshare lot", "casio exilim lot"
    ],

    # G6: big-lot explicit
    [
        "huge camera lot", "massive camera lot", "large camera lot", "big camera lot",
        "bulk cameras lot", "box of cameras", "crate of cameras", "bag of cameras",
        "bundle of cameras", "job lot of cameras"
    ]
]

def pick_group() -> int:
    # se schimbă la fiecare run (~5 minute), ciclic
    return int(time.time() // 300) % len(SEARCH_GROUPS)

def main():
    seen = load_seen()
    alerts = []

    group_idx = pick_group()
    terms = SEARCH_GROUPS[group_idx]

    urls = []
    for cat in CATEGORIES:
        for term in terms:
            urls.append(build_url(term, cat))

    for url in urls:
        try:
            results = fetch_search(url)
        except Exception as e:
            print("Fetch error:", e)
            continue

        for it in results:
            if it["id"] in seen:
                continue
            seen.add(it["id"])

            s = score_listing(it["title"])
            if s >= 3:
                alerts.append((s, it))

        time.sleep(1.2)

    save_seen(seen)

    alerts.sort(key=lambda x: x[0], reverse=True)
    alerts = alerts[:7]

    for s, it in alerts:
        qty = extract_qty(it["title"])
        qty_txt = f"Qty: {qty}" if qty is not None else "Qty: unknown"

        # ✅ 2 niveluri: portocaliu + roșu
        if s >= 12:
            label = "🔴 HUGE LOT 🔴"
        elif s >= 8:
            label = "🟠 VERY GOOD LOT 🟠"
        else:
            label = "🟢 Lot"

        msg = (
            f"{label}\n"
            f"Group: {group_idx}\n"
            f"Score: {s}\n"
            f"{qty_txt}\n"
            f"{it['title']}\n"
            f"{it['price']}\n"
            f"{it['link']}"
        )
        tg_send(msg)

    print(f"Done. Group={group_idx} | URLs={len(urls)} | Alerts={len(alerts)} | Seen={len(seen)}")

if __name__ == "__main__":
    main()
