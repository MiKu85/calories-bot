# MVP Scope

## What is in MVP

- Subscription gate: user must subscribe to Telegram channel @healthy_normal before accessing the bot
- Onboarding: collects name, sex, age, height, weight, activity level, workouts/week, goal
- Target calculation: Mifflin-St Jeor formula + activity multiplier + goal correction
- Macro targets: protein (goal-dependent g/kg), fat (0.8 g/kg), carbs (remainder)
- Meal logging from text, Telegram voice messages, and photos
- Voice transcription via STT (Whisper), then same pipeline as text
- Photo analysis: detect food items and approximate portions; low-confidence photo results prompt user to clarify
- After each meal: show meal KBJU + daily consumed + daily remaining
- Inline actions after meal: confirm, correct (re-enter by text/voice), view today's stats
- Commands: /start, /profile, /stats, /help, /reset
- /profile: show profile + targets + edit weight/activity/goal/recalculate/reset
- /reset: reset onboarding / day progress / full profile
- /stats: today consumed and remaining calories + macros, meal count, status phrase
- Admin commands restricted by telegram ID: user counts, active today, new today, meals today, onboarding completions, latest errors
- Feedback flow: triggered once, 7 days after first saved meal
- PostgreSQL for all storage
- Railway-friendly deployment with Docker

## What is NOT in MVP

- Payments or subscriptions
- Weekly or monthly analytics
- Google Sheets or any export
- Ingredient-level portion editor
- Food preference or allergy collection
- Integration with external food databases
- Reminders or push notifications
- Referral mechanics
- Web admin panel
- Advanced retention analytics
- Training type or detailed fitness tracking

## Known limitations

- Calorie and macro estimates are approximate, especially for photos and voice
- Photo analysis accuracy depends on image quality, lighting, and food complexity
- Portion sizes are estimated, not measured
- The bot does not distinguish between cooking methods (boiled vs fried) unless explicitly mentioned
- Voice transcription may make errors with uncommon food names
- Daily aggregate is based on UTC date by default; timezone support is not in MVP

## Honest statement on photo estimates

Photo-based calorie and macro estimates are **approximate**. The bot uses AI vision to identify food items and estimate portions from a single image. Results can vary significantly depending on plate size, camera angle, food overlap, and lighting. When confidence is low, the bot will say so and ask for clarification rather than guess.

**Do not use photo estimates as a basis for medical or therapeutic nutrition decisions.**

## Medical disclaimer

This bot is a convenience tool for personal calorie and macro tracking. It does not replace advice from a qualified nutritionist, dietitian, or medical professional. If you have health conditions, dietary restrictions, or medical needs, consult a qualified specialist.
