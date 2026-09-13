"""
Регрессия на «бот молчит»: человек нажал «💾 В мои блюда», бот спросил название,
а в ответ полетели фото тарелки — и они уходили в пустоту, потому что фото-хендлер
принимал только состояния None/correction/patch, а fallback-хендлера не было.
"""
from __future__ import annotations

from bot.handlers.photo import _PHOTO_IS_MEAL_IN
from bot.handlers.saved_meals import SavedMealStates, _too_long_hint, _valid_name
from bot.handlers.voice import _VOICE_IS_MEAL_IN


class TestPhotoAndVoiceEscapeSavedMealDialog:
    def test_photo_is_meal_while_waiting_for_name(self):
        assert SavedMealStates.waiting_name in _PHOTO_IS_MEAL_IN
        assert SavedMealStates.waiting_rename in _PHOTO_IS_MEAL_IN

    def test_voice_is_meal_while_waiting_for_name(self):
        assert SavedMealStates.waiting_name in _VOICE_IS_MEAL_IN
        assert SavedMealStates.waiting_rename in _VOICE_IS_MEAL_IN

    def test_idle_state_still_accepted(self):
        assert None in _PHOTO_IS_MEAL_IN and None in _VOICE_IS_MEAL_IN


class TestLongNameHint:
    def test_description_is_rejected_as_name(self):
        description = (
            "Котлеты из куриной грудки и кабачка 100 гр. Салат из рукколы и редиса "
            "100 гр. Соленый огурец. Кусочек цельнозернового хлеба"
        )
        assert _valid_name(description) is None

    def test_hint_shows_length_and_way_out(self):
        description = "к" * 127
        hint = _too_long_hint(description)
        assert "127" in hint
        assert "/cancel" in hint

    def test_short_name_passes(self):
        assert _valid_name("  Котлеты с салатом ") == "Котлеты с салатом"


class TestFallbackRouterIsLast:
    def test_fallback_registered_after_meal_router(self):
        import main

        dp = main.create_dispatcher()
        names = [r.name for r in dp.sub_routers]
        assert names[-1] == "fallback", names
        assert names.index("meal") < names.index("fallback")
