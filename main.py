from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from models import MenuResponse
from psu_scraper import fetch_menu_with_nutrition, HALLS

app = FastAPI(title="NittanyEats – Live PSU Menus with Nutrition & CO2e")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/halls")
def list_halls():
    """
    Returns PSU halls (location IDs and names) that you can pass as campus_id.
    """
    return [{"id": k, "name": v} for k, v in HALLS.items()]


@app.get("/menus", response_model=MenuResponse)
def get_menus(
    campus_id: str = Query(..., description="PSU Location ID (11, 14, 13, 16, 17)"),
    meal: str = Query(..., description="Breakfast, Lunch, Dinner, Late Night"),
    date: str = Query(..., description="Date in PSU format, e.g. 12/1/25"),
):
    """
    Live PSU menu with nutrition + CO2e + sustainability score.

    Example:
      /menus?campus_id=14&meal=Lunch&date=12/1/25
    """
    return fetch_menu_with_nutrition(campus_id, meal, date)


@app.get("/recommendations", response_model=MenuResponse)
def recommendations(
    campus_id: str = Query(...),
    meal: str = Query(...),
    date: str = Query(...),
):
    """
    Same as /menus but items are sorted by sustainability score (descending).
    """
    data = fetch_menu_with_nutrition(campus_id, meal, date)
    sorted_items = sorted(
        data.items,
        key=lambda i: i.sustainability_score,
        reverse=True,
    )
    data.items = sorted_items
    return data


@app.get("/health")
def health():
    return {"status": "ok", "service": "nittanyeats-backend", "features": ["live_menu", "nutrition", "co2e"]}
