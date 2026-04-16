from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Enums ──────────────────────────────────────────────────────────────────────

class Sex(str, enum.Enum):
    male = "male"
    female = "female"


class ActivityLevel(str, enum.Enum):
    sedentary = "sedentary"          # no workouts / desk job
    light = "light"                  # 1-2 workouts/week
    moderate = "moderate"            # 3-4 workouts/week
    active = "active"                # 5+ workouts/week
    very_active = "very_active"      # athlete / physical job


class Goal(str, enum.Enum):
    lose = "lose"
    maintain = "maintain"
    gain = "gain"


class OnboardingState(str, enum.Enum):
    new = "new"                      # just started
    awaiting_name = "awaiting_name"
    awaiting_sex = "awaiting_sex"
    awaiting_age = "awaiting_age"
    awaiting_height = "awaiting_height"
    awaiting_weight = "awaiting_weight"
    awaiting_activity = "awaiting_activity"
    awaiting_workouts = "awaiting_workouts"
    awaiting_goal = "awaiting_goal"
    completed = "completed"


class MealInputType(str, enum.Enum):
    text = "text"
    voice = "voice"
    photo = "photo"


class ConfidenceLevel(str, enum.Enum):
    high = "high"
    medium = "medium"
    low = "low"


class EventType(str, enum.Enum):
    onboarding_completed = "onboarding_completed"
    meal_logged = "meal_logged"
    meal_corrected = "meal_corrected"
    feedback_sent = "feedback_sent"
    error = "error"
    subscription_check = "subscription_check"


# ── Models ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64))

    # Profile
    preferred_name: Mapped[str | None] = mapped_column(String(64))
    sex: Mapped[Sex | None] = mapped_column(Enum(Sex))
    age: Mapped[int | None] = mapped_column(Integer)
    height_cm: Mapped[float | None] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    activity_level: Mapped[ActivityLevel | None] = mapped_column(Enum(ActivityLevel))
    workouts_per_week: Mapped[int | None] = mapped_column(Integer)
    goal: Mapped[Goal | None] = mapped_column(Enum(Goal))

    # Calculated targets (null until profile is complete)
    daily_calories_target: Mapped[float | None] = mapped_column(Float)
    daily_protein_g_target: Mapped[float | None] = mapped_column(Float)
    daily_fat_g_target: Mapped[float | None] = mapped_column(Float)
    daily_carbs_g_target: Mapped[float | None] = mapped_column(Float)

    # State
    onboarding_state: Mapped[OnboardingState] = mapped_column(
        Enum(OnboardingState), default=OnboardingState.new, nullable=False
    )
    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_meal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Morning summary
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False, server_default="Europe/Moscow")
    morning_sent_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Inactivity reminders
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    inactivity_reminder_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    meals: Mapped[list[Meal]] = relationship("Meal", back_populates="user", lazy="noload")
    daily_aggregates: Mapped[list[DailyAggregate]] = relationship(
        "DailyAggregate", back_populates="user", lazy="noload"
    )
    feedback: Mapped[FeedbackRecord | None] = relationship(
        "FeedbackRecord", back_populates="user", uselist=False, lazy="noload"
    )

    @property
    def profile_complete(self) -> bool:
        return all([self.sex, self.age, self.height_cm, self.weight_kg])

    @property
    def targets_set(self) -> bool:
        return self.daily_calories_target is not None


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    input_type: Mapped[MealInputType] = mapped_column(Enum(MealInputType), nullable=False)
    raw_input: Mapped[str | None] = mapped_column(Text)  # text or voice transcription

    # Nutrition
    calories: Mapped[float] = mapped_column(Float, nullable=False)
    protein_g: Mapped[float] = mapped_column(Float, nullable=False)
    fat_g: Mapped[float] = mapped_column(Float, nullable=False)
    carbs_g: Mapped[float] = mapped_column(Float, nullable=False)

    # AI analysis details
    confidence: Mapped[ConfidenceLevel] = mapped_column(
        Enum(ConfidenceLevel), default=ConfidenceLevel.medium, nullable=False
    )
    confidence_notes: Mapped[str | None] = mapped_column(Text)
    meal_items: Mapped[list | None] = mapped_column(JSON)  # [{name, portion, calories, ...}]

    # State
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="meals")


class DailyAggregate(Base):
    __tablename__ = "daily_aggregates"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_daily_aggregate_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)

    total_calories: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_protein_g: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_fat_g: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_carbs_g: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    meals_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="daily_aggregates")


class FeedbackRecord(Base):
    __tablename__ = "feedback_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True)

    feedback_text: Mapped[str | None] = mapped_column(Text)
    has_voice_comment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="feedback")


class EventLog(Base):
    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
