from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def meal_result_kb(
    meal_id: int,
    *,
    prev_meal_id: int | None = None,
    # Legacy params kept for call-site backward compat — ignored
    show_clarify: bool = False,
    show_augment: bool = True,
) -> InlineKeyboardMarkup:
    """
    Keyboard shown after a meal is saved (pending user confirmation).

    Two primary buttons: confirm and edit (edit handles add/remove/change).
    prev_meal_id: show merge button when LLM split one batch into multiple meals.
    """
    builder = InlineKeyboardBuilder()

    # Row 1: confirm + edit
    builder.button(text="✅ Верно", callback_data=f"meal_ok:{meal_id}")
    builder.button(text="🔧 Исправить", callback_data=f"meal_fix:{meal_id}")

    # Merge button — only when LLM split a batch and user wants to undo it
    if prev_meal_id is not None:
        builder.button(
            text="🔗 Объединить с предыдущим",
            callback_data=f"meal_merge:{prev_meal_id}:{meal_id}",
        )

    # Row 2: delete + stats
    builder.button(text="🗑 Удалить", callback_data=f"meal_delete:{meal_id}")
    builder.button(text="📈 Мой день", callback_data="meal_stats")

    # Row 3: save as reusable template («Мои блюда»). Available before and after
    # a correction, so a fixed-up meal (e.g. protein shake) can be saved accurately.
    builder.button(text="💾 В мои блюда", callback_data=f"meal_save_tpl:{meal_id}")

    rows = [2]
    if prev_meal_id is not None:
        rows.append(1)
    rows.append(2)  # delete + stats
    rows.append(1)  # save as template

    builder.adjust(*rows)
    return builder.as_markup()


def meal_confirm_kb(meal_id: int) -> InlineKeyboardMarkup:
    """
    Keyboard shown in the confirmation message after ✅ Верно.
    Contains the optional rating button.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Оценить этот приём", callback_data=f"meal_detail:{meal_id}")
    builder.adjust(1)
    return builder.as_markup()


def day_meals_kb(
    meals: list,
    target_date,
    *,
    tz_name: str | None = None,
    has_prev: bool = True,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    """
    Keyboard for the diary screen: fix/delete per meal plus day navigation.

    The buttons under the original result message are easy to miss once the chat
    has scrolled on, so this is the permanent way to edit any past meal.
    Meal ids carry the date so the screen can be redrawn after a change.
    """
    from datetime import timedelta

    from bot.utils.tz import local_time

    builder = InlineKeyboardBuilder()
    rows: list[int] = []
    iso = target_date.isoformat()

    for meal in meals:
        time_label = local_time(meal.logged_at, tz_name)
        builder.button(
            text=f"🔧 {time_label} · {round(meal.calories)} ккал",
            callback_data=f"meal_fix_day:{meal.id}:{iso}",
        )
        builder.button(text="🗑", callback_data=f"meal_del_day:{meal.id}:{iso}")
        rows.append(2)

    nav = 0
    if has_prev:
        prev_day = (target_date - timedelta(days=1)).isoformat()
        builder.button(text="← предыдущий день", callback_data=f"day_nav:{prev_day}")
        nav += 1
    if has_next:
        next_day = (target_date + timedelta(days=1)).isoformat()
        builder.button(text="следующий день →", callback_data=f"day_nav:{next_day}")
        nav += 1
    if nav:
        rows.append(nav)

    builder.adjust(*rows)
    return builder.as_markup()


def duplicate_check_kb(new_meal_id: int) -> InlineKeyboardMarkup:
    """
    Keyboard shown when a possible duplicate meal is detected.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Новый приём", callback_data=f"dedup_ok:{new_meal_id}")
    builder.button(text="Это повтор — удалить", callback_data=f"dedup_delete:{new_meal_id}")
    builder.adjust(2)
    return builder.as_markup()
