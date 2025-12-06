import re
from typing import List, Dict, Optional
from urllib.parse import urljoin, parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from models import Nutrition, MenuItem, MenuResponse


BASE_URL = "https://www.absecom.psu.edu/menus/user-pages/"
DAILY_MENU_URL = urljoin(BASE_URL, "daily-menu.cfm")

# Campus / hall IDs – from the Location dropdown
HALLS: Dict[str, str] = {
    "11": "East Food District @ Findlay",
    "17": "North Food District @ Warnock",
    "14": "Pollock Dining Commons",
    "13": "South Food District @ Redifer",
    "16": "West Food District @ Waring",
}

# ------------ helper: HTTP session (simple in-memory reuse) ------------

_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "NittanyEats/1.0"
                )
            }
        )
    return _session


# ------------ CO2e estimation (Option A heuristic) ------------

def estimate_co2e_kg(name: str, nutrition: Optional[Nutrition]) -> float:
    """
    Very rough CO2e estimate per serving using Option A categories.
    Values are kg CO2e / serving, tuned from typical kg CO2e per kg.
    """
    n = name.lower()

    # protein source
    if any(k in n for k in ["beef", "steak", "brisket", "meatball", "burger", "pepperoni"]):
        base = 5.0    # beef-heavy
    elif any(k in n for k in ["pork", "bacon", "ham", "sausage"]):
        base = 4.0
    elif any(k in n for k in ["chicken", "turkey", "tender", "wing"]):
        base = 3.0
    elif any(k in n for k in ["fish", "salmon", "tuna", "shrimp", "seafood"]):
        base = 3.5
    elif any(k in n for k in ["cheese", "mac & cheese", "alfredo", "queso"]):
        base = 3.0
    elif any(k in n for k in ["egg", "omelet", "omelette"]):
        base = 2.0
    elif any(k in n for k in ["tofu", "tempeh", "seitan", "lentil", "bean", "falafel", "veggie", "vegetarian", "vegan"]):
        base = 1.0
    else:
        # carbs / mixed / unknown
        base = 2.0

    # adjust by calories a bit (bigger meals → more food → more emissions)
    if nutrition and nutrition.calories:
        c = nutrition.calories
        if c > 900:
            base *= 1.4
        elif c > 700:
            base *= 1.25
        elif c > 500:
            base *= 1.1
        elif c < 300:
            base *= 0.85

    return round(base, 2)


def sustainability_score_from_co2e(co2e_per_serving: float) -> float:
    """
    Simple scoring: 100 - 10 * CO2e, clamped 0-100.
    Lower CO2e → higher score.
    """
    score = 100.0 - 10.0 * co2e_per_serving
    if co2e_per_serving <= 1.5:
        score += 10  # bonus for super low
    return max(0.0, min(score, 100.0))


# ------------ Nutrition scraping ------------

def _extract_first_number(text: str) -> Optional[float]:
    m = re.search(r"(\d+(\.\d+)?)", text.replace(",", ""))
    return float(m.group(1)) if m else None


def scrape_nutrition(mid: str) -> Nutrition:
    """
    Scrape nutrition-label.cfm?mid=... for one item.
    We use multiple heuristics based on the screenshot you shared.
    """
    url = urljoin(BASE_URL, f"nutrition-label.cfm?mid={mid}")
    s = get_session()
    resp = s.get(url, timeout=10)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    nut = Nutrition()

    # --- left panel: Serving Size, Calories, Calories from Fat ---
    # These appear as lines of text like "Serving Size: 6 OZ"
    left_block = soup.find("th", class_="top")
    if left_block:
        # parent row, then next sibling rows contain text
        container = left_block.parent.parent if left_block.parent else None
        if container:
            text = container.get_text("\n", strip=True)
            for line in text.splitlines():
                line = line.strip()
                if line.lower().startswith("serving size"):
                    nut.serving_size = line.split(":", 1)[-1].strip()
                elif line.lower().startswith("calories:"):
                    val = _extract_first_number(line)
                    nut.calories = int(val) if val is not None else None
                elif line.lower().startswith("calories from fat"):
                    val = _extract_first_number(line)
                    nut.calories_from_fat = int(val) if val is not None else None

    # --- right side big table: Total Fat, Protein, Carbs, Sodium, etc. ---
    # There is a table with class "responsive-table" and rows containing
    # e.g. "Total Fat 25.6g", "% DV", "Total Carb 10.4g" etc.
    main_table = soup.find("table", class_="responsive-table") or soup.find("table")
    if main_table:
        for td in main_table.find_all("td"):
            text = td.get_text(" ", strip=True)
            low = text.lower()

            if "total fat" in low and nut.total_fat_g is None:
                nut.total_fat_g = _extract_first_number(text)
            elif "sat fat" in low and nut.sat_fat_g is None:
                nut.sat_fat_g = _extract_first_number(text)
            elif "trans fat" in low and nut.trans_fat_g is None:
                nut.trans_fat_g = _extract_first_number(text)
            elif "total carb" in low and nut.carbs_g is None:
                nut.carbs_g = _extract_first_number(text)
            elif "dietary fiber" in low and nut.fiber_g is None:
                nut.fiber_g = _extract_first_number(text)
            elif "sugars" in low and "added" not in low and nut.sugars_g is None:
                nut.sugars_g = _extract_first_number(text)
            elif "added sugar" in low and nut.added_sugars_g is None:
                nut.added_sugars_g = _extract_first_number(text)
            elif "protein" in low and nut.protein_g is None:
                nut.protein_g = _extract_first_number(text)
            elif "sodium" in low and nut.sodium_mg is None:
                nut.sodium_mg = _extract_first_number(text)

    return nut


# ------------ Menu scraping ------------

# simple in-memory cache: (campus_id, meal, date) → MenuResponse
_menu_cache: Dict[str, MenuResponse] = {}


def _cache_key(campus_id: str, meal: str, date_str: str) -> str:
    return f"{campus_id}|{meal}|{date_str}"


def fetch_menu_with_nutrition(campus_id: str, meal: str, date_str: str) -> MenuResponse:
    """
    Scrape the PSU daily menu for a campus + meal + date.

    campus_id: "11" (East), "14" (Pollock), etc.
    meal: "Breakfast" | "Lunch" | "Dinner" | "Late Night"
    date_str: "12/1/25" (as used by PSU)
    """
    key = _cache_key(campus_id, meal, date_str)
    if key in _menu_cache:
        return _menu_cache[key]

    payload = {
        "selCampus": campus_id,
        "selMeal": meal,
        "selMenuDate": date_str,
        "submit": "Submit",
    }

    s = get_session()
    resp = s.post(DAILY_MENU_URL, data=payload, timeout=10)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    hall_name = HALLS.get(campus_id, f"Campus {campus_id}")
    items: List[MenuItem] = []
    next_id = 1

    # Layout you showed:
    # <h2 class="category-header" ...>ENTREES</h2>
    # <div class="menu-items">
    #   <a href="nutrition-label.cfm?mid=147960642" aria-label="Buffalo Chicken Mac & Cheese">...</a>
    #   ...
    # </div>
    for cat in soup.find_all("h2", class_="category-header"):
        category = cat.get_text(strip=True)
        menu_div = cat.find_next("div", class_="menu-items")
        if not menu_div:
            continue

        for a in menu_div.find_all("a"):
            name = a.get("aria-label") or a.get_text(strip=True)
            if not name:
                continue

            href = a.get("href") or ""
            mid = None
            if "nutrition-label.cfm" in href:
                full_url = urljoin(BASE_URL, href)
                qs = parse_qs(urlparse(full_url).query)
                mid_list = qs.get("mid")
                if mid_list:
                    mid = mid_list[0]

            nutrition = scrape_nutrition(mid) if mid else None
            co2e = estimate_co2e_kg(name, nutrition)
            score = sustainability_score_from_co2e(co2e)

            items.append(
                MenuItem(
                    id=next_id,
                    hall_id=campus_id,
                    hall_name=hall_name,
                    name=name,
                    category=category,
                    meal_period=meal,
                    mid=mid,
                    nutrition=nutrition,
                    co2e_per_serving=co2e,
                    sustainability_score=score,
                )
            )
            next_id += 1

    menu_resp = MenuResponse(
        hall_id=campus_id,
        hall_name=hall_name,
        meal_period=meal,
        date=date_str,
        items=items,
    )

    _menu_cache[key] = menu_resp
    return menu_resp

