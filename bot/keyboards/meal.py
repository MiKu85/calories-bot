from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def meal_result_kb(
    meal_id: int,
    *,
    show_clarify: bool = False,
    show_augment: bool = True,
    prev_meal_id: int | None = None,
) -> InlineKeyboardMarkup:
    """
    Keyboard shown after a meal is saved (pending user confirmation).

    show_clarify:  show "🔍 Уточнить" (for photo meals or medium/low confidence).
    show_augment:  show "➕ Дополнить" (Layer 2 — add items to existing meal).
                   Hidden after MEAL_AUGMENT_WINDOW_MINUTES or when user confirms.
    prev_meal_id:  when set, show "🔗 Объединить с предыдущим" — only used when
                   the LLM split one debounce batch into multiple meals and the
                   user wants to merge this meal back into the previous one.
    """
    builder = InlineKeyboardBuilder()

    # Row 1: confirm + fix
    builder.button(text="✅ Верно", callback_data=f"meal_ok:{meal_id}")
    builder.button(text="✏️ Исправить", callback_data=f"meal_fix:{meal_id}")

    # Row 2: augment (optional)
    if show_augment:
        builder.button(text="➕ Дополнить", callback_data=f"meal_augment:{meal_id}")

    # Row 3: clarify (optional, photo / low-confidence)
    if show_clarify:
        builder.button(text="🔍 Уточнить", callback_data=f"meal_clarify:{meal_id}")

    # Merge button — only when LLM split a batch and user wants to undo it
    if prev_meal_id is not None:
        builder.button(
            text="🔗 Объединить с предыдущим",
            callback_data=f"meal_merge:{prev_meal_id}:{meal_id}",
        )

    # Stats always last
    builder.button(text="📊 Статистика за сегодня", callback_data="meal_stats")

    # Layout: 2 on row 1, then 1 per row for the rest
    rows = [2]
    if show_augment:
        rows.append(1)
    if show_clarify:
        rows.append(1)
    if prev_meal_id is not None:
        rows.append(1)
    rows.append(1)  # stats

    builder.adjust(*rows)
    return builder.as_markup()


def meal_detail_kb(meal_id: int) -> InlineKeyboardMarkup:
    """
    Keyboard shown after confirmation (rating block visible).
    Only "🎯 Подробнее" remains — for the full nutritionist assessment.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Подробнее", callback_data=f"meal_detail:{meal_id}")
    builder.adjust(1)
    return builder.as_markup()


def duplicate_check_kb(new_meal_id: int) -> InlineKeyboardMarkup:
    """
    Keyboard shown when a possible duplicate meal is detected.

    new_meal_id: id of the just-saved meal that might be a duplicate.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Новый приём", callback_data=f"dedup_ok:{new_meal_id}")
    builder.button(text="Это повтор — удалить", callback_data=f"dedup_delete:{new_meal_id}")
    builder.adjust(2)
    return builder.as_markup()
