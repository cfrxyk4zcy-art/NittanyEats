from typing import Optional, List
from pydantic import BaseModel


class Nutrition(BaseModel):
    serving_size: Optional[str] = None
    calories: Optional[int] = None
    calories_from_fat: Optional[int] = None
    total_fat_g: Optional[float] = None
    sat_fat_g: Optional[float] = None
    trans_fat_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sugars_g: Optional[float] = None
    added_sugars_g: Optional[float] = None
    protein_g: Optional[float] = None
    sodium_mg: Optional[float] = None


class MenuItem(BaseModel):
    id: int
    hall_id: str
    hall_name: str
    name: str
    category: str
    meal_period: str
    mid: Optional[str] = None           # PSU menu item ID
    nutrition: Optional[Nutrition] = None
    co2e_per_serving: float
    sustainability_score: float


class MenuResponse(BaseModel):
    hall_id: str
    hall_name: str
    meal_period: str
    date: str
    items: List[MenuItem]

