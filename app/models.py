"""SQLAlchemy ORM models.

Lifecycle of food:
  Restaurant dispatches a SurplusBatch (INCOMING)
    -> Dark store receives it (AVAILABLE, sold to night workers via Orders)
    -> near expiry / at the 3:30 AM cut-off, leftovers become a ReliefBundle (batch RELIEF)
    -> NGO claims -> picks up -> distributes.
"""
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, now


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    # worker | restaurant | darkstore | ngo | admin
    role: Mapped[str] = mapped_column(String(20), index=True)
    restaurant_id: Mapped[int | None] = mapped_column(ForeignKey("restaurants.id"))
    dark_store_id: Mapped[int | None] = mapped_column(ForeignKey("dark_stores.id"))
    ngo_id: Mapped[int | None] = mapped_column(ForeignKey("ngos.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Restaurant(Base):
    __tablename__ = "restaurants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    area: Mapped[str] = mapped_column(String(80))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    closing_time: Mapped[str] = mapped_column(String(5), default="23:00")  # HH:MM
    avg_footfall: Mapped[int] = mapped_column(Integer, default=300)
    dark_store_id: Mapped[int | None] = mapped_column(ForeignKey("dark_stores.id"))


class DarkStore(Base):
    __tablename__ = "dark_stores"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    area: Mapped[str] = mapped_column(String(80))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    hot_capacity: Mapped[int] = mapped_column(Integer, default=400)
    chilled_capacity: Mapped[int] = mapped_column(Integer, default=600)


class NGO(Base):
    __tablename__ = "ngos"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    area: Mapped[str] = mapped_column(String(80))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    capacity_per_night: Mapped[int] = mapped_column(Integer, default=200)


class SurplusBatch(Base):
    __tablename__ = "surplus_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    dark_store_id: Mapped[int] = mapped_column(ForeignKey("dark_stores.id"), index=True)
    dish_name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40))
    diet: Mapped[str] = mapped_column(String(10))           # VEG | NON_VEG | VEGAN
    allergens: Mapped[str] = mapped_column(String(200), default="")  # comma separated
    storage: Mapped[str] = mapped_column(String(10))        # HOT | CHILLED
    price: Mapped[float] = mapped_column(Float, default=60.0)
    cooked_at: Mapped[datetime] = mapped_column(DateTime)
    shelf_life_hours: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    received_at: Mapped[datetime | None] = mapped_column(DateTime)
    entry_temp_c: Mapped[float | None] = mapped_column(Float)
    initial_qty: Mapped[int] = mapped_column(Integer)
    available_qty: Mapped[int] = mapped_column(Integer)
    sold_qty: Mapped[int] = mapped_column(Integer, default=0)
    # INCOMING | AVAILABLE | SOLD_OUT | RELIEF | EXPIRED | DISCARDED | REJECTED
    status: Mapped[str] = mapped_column(String(12), index=True, default="INCOMING")
    temp_breach: Mapped[bool] = mapped_column(Boolean, default=False)
    shelf_life_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    status_note: Mapped[str] = mapped_column(String(200), default="")

    restaurant: Mapped[Restaurant] = relationship()
    dark_store: Mapped[DarkStore] = relationship()


class TemperatureReading(Base):
    __tablename__ = "temperature_readings"
    id: Mapped[int] = mapped_column(primary_key=True)
    dark_store_id: Mapped[int] = mapped_column(ForeignKey("dark_stores.id"), index=True)
    zone: Mapped[str] = mapped_column(String(10))  # HOT | CHILLED
    temp_c: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    in_range: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    dark_store_id: Mapped[int] = mapped_column(ForeignKey("dark_stores.id"))
    delivery_address: Mapped[str] = mapped_column(String(200))
    total: Mapped[float] = mapped_column(Float)
    # PLACED | PACKED | OUT_FOR_DELIVERY | DELIVERED
    status: Mapped[str] = mapped_column(String(20), default="PLACED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("surplus_batches.id"), index=True)
    dish_name: Mapped[str] = mapped_column(String(120))
    qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column(Float)

    order: Mapped[Order] = relationship(back_populates="items")


class ReliefBundle(Base):
    __tablename__ = "relief_bundles"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("surplus_batches.id"), unique=True)
    dark_store_id: Mapped[int] = mapped_column(ForeignKey("dark_stores.id"), index=True)
    servings: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(120))
    # FLAGGED | CLAIMED | IN_TRANSIT | DISTRIBUTED | EXPIRED
    status: Mapped[str] = mapped_column(String(12), index=True, default="FLAGGED")
    ngo_id: Mapped[int | None] = mapped_column(ForeignKey("ngos.id"), index=True)
    flagged_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime)
    distributed_at: Mapped[datetime | None] = mapped_column(DateTime)
    shelter_name: Mapped[str | None] = mapped_column(String(120))
    people_fed: Mapped[int | None] = mapped_column(Integer)

    batch: Mapped[SurplusBatch] = relationship()
    dark_store: Mapped[DarkStore] = relationship()
    ngo: Mapped[NGO | None] = relationship()


class DailySurplusLog(Base):
    """One row per restaurant x dish category x day. Training data for the forecaster."""
    __tablename__ = "daily_surplus_logs"
    __table_args__ = (UniqueConstraint("restaurant_id", "log_date", "dish_category"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    log_date: Mapped[date] = mapped_column(Date, index=True)
    dish_category: Mapped[str] = mapped_column(String(40))
    weather_condition: Mapped[str] = mapped_column(String(12))
    temperature: Mapped[float] = mapped_column(Float)
    is_holiday: Mapped[bool] = mapped_column(Boolean)
    is_festival: Mapped[bool] = mapped_column(Boolean)
    historical_avg_footfall: Mapped[int] = mapped_column(Integer)
    actual_footfall: Mapped[int] = mapped_column(Integer)
    prep_count: Mapped[int] = mapped_column(Integer)
    sold_count: Mapped[int] = mapped_column(Integer)
    surplus_servings: Mapped[int] = mapped_column(Integer)
    closing_time: Mapped[str] = mapped_column(String(5), default="23:00")


class DailyOperations(Base):
    """Nightly roll-up per dark store, used for long-run impact analytics."""
    __tablename__ = "daily_operations"
    __table_args__ = (UniqueConstraint("dark_store_id", "op_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dark_store_id: Mapped[int] = mapped_column(ForeignKey("dark_stores.id"))
    op_date: Mapped[date] = mapped_column(Date, index=True)
    servings_received: Mapped[int] = mapped_column(Integer)
    servings_sold: Mapped[int] = mapped_column(Integer)
    servings_donated: Mapped[int] = mapped_column(Integer)
    servings_wasted: Mapped[int] = mapped_column(Integer)
    relief_expired: Mapped[int] = mapped_column(Integer, default=0)  # flagged for NGOs but never collected
    workers_served: Mapped[int] = mapped_column(Integer)
    people_fed: Mapped[int] = mapped_column(Integer)


class NgoDistributionLog(Base):
    """Historical NGO distributions (seeded) for the analytics totals."""
    __tablename__ = "ngo_distribution_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    ngo_id: Mapped[int] = mapped_column(ForeignKey("ngos.id"), index=True)
    log_date: Mapped[date] = mapped_column(Date, index=True)
    servings: Mapped[int] = mapped_column(Integer)
    people_fed: Mapped[int] = mapped_column(Integer)


class WeatherDay(Base):
    """One row per night, condensed from the weather API (see services/weather.py)."""
    __tablename__ = "weather_days"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    condition: Mapped[str] = mapped_column(String(12))
    temperature: Mapped[float] = mapped_column(Float)        # mean 6-11 PM
    temp_max: Mapped[float] = mapped_column(Float)
    evening_precip_mm: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="open-meteo")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text)   # shown to hub managers, so written in plain language
    dark_store_id: Mapped[int | None] = mapped_column(ForeignKey("dark_stores.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
