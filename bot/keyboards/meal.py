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

    # Day stats always last
    builder.button(text="📈 Мой день", callback_data="meal_stats")

    rows = [2]
    if prev_meal_id is not None:
        rows.append(1)
    rows.append(1)  # stats

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


def duplicate_check_kb(new_meal_id: int) -> InlineKeyboardMarkup:
    """
    Keyboard shown when a possible duplicate meal is detected.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Новый приём", callback_data=f"dedup_ok:{new_meal_id}")
    builder.button(text="Это повтор — удалить", callback_data=f"dedup_delete:{new_meal_id}")
    builder.adjust(2)
    return builder.as_markup()
