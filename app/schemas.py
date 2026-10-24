from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ml.calendar_data import WEATHER_CONDITIONS
from app.ml.synthetic import CATEGORIES

Weather = Literal["Clear", "Cloudy", "Rain", "Heavy Rain", "Heatwave"]
Category = Literal["Biryani & Rice", "Curries", "Breads", "Snacks", "Desserts"]
assert set(Weather.__args__) == set(WEATHER_CONDITIONS) and set(Category.__args__) == set(CATEGORIES)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=120)
    password: str = Field(min_length=8, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    role: str
    restaurant_id: int | None
    dark_store_id: int | None
    ngo_id: int | None


class TokenOut(BaseModel):
    token: str
    user: UserOut


class PredictIn(BaseModel):
    date: date
    # Omit weather to use the automatic forecast (Open-Meteo, or the seasonal estimate beyond its range).
    weather_condition: Weather | None = None
    temperature: float | None = Field(default=None, ge=-10, le=55)
    is_holiday: bool | None = None       # None -> look up the holiday calendar
    is_festival: bool | None = None      # None -> look up the festival calendar
    historical_avg_footfall: int | None = Field(default=None, ge=10, le=10000)
    dish_category: Category | None = None  # None -> all categories
    restaurant_id: int | None = None


class DailyLogEntry(BaseModel):
    dish_category: Category
    prep_count: int = Field(ge=0, le=5000)
    sold_count: int = Field(ge=0, le=5000)

    @field_validator("sold_count")
    @classmethod
    def sold_le_prep(cls, v, info):
        if "prep_count" in info.data and v > info.data["prep_count"]:
            raise ValueError("Sold can't be more than prepared")
        return v


class DailyLogIn(BaseModel):
    closing_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    actual_footfall: int = Field(ge=0, le=10000)
    weather_condition: Weather | None = None
    temperature: float | None = Field(default=None, ge=-10, le=55)
    entries: list[DailyLogEntry] = Field(min_length=1, max_length=len(CATEGORIES))


class DispatchIn(BaseModel):
    dish_name: str = Field(min_length=2, max_length=120)
    dish_category: Category
    diet: Literal["VEG", "NON_VEG", "VEGAN"]
    allergens: list[str] = Field(default_factory=list, max_length=14)
    storage: Literal["HOT", "CHILLED"]
    cooked_at: datetime
    shelf_life_hours: float = Field(gt=0, le=48)
    quantity: int = Field(ge=1, le=500)
    price: float = Field(default=60, ge=0, le=1000)
    dark_store_id: int | None = None


class ReceiveIn(BaseModel):
    entry_temp_c: float = Field(ge=-30, le=110)


class ShelfLifeIn(BaseModel):
    expires_at: datetime
    reason: str = Field(min_length=3, max_length=150)


class ExcursionIn(BaseModel):
    zone: Literal["HOT", "CHILLED"]
    minutes: int = Field(default=3, ge=1, le=30)


class SweepIn(BaseModel):
    force_cutoff: bool = False


class CartLine(BaseModel):
    batch_id: int
    qty: int = Field(ge=1, le=10)


class OrderIn(BaseModel):
    dark_store_id: int
    items: list[CartLine] = Field(min_length=1, max_length=20)
    delivery_address: str = Field(min_length=5, max_length=200)


class DistributeIn(BaseModel):
    shelter_name: str = Field(min_length=2, max_length=120)
    people_fed: int = Field(ge=1, le=5000)
