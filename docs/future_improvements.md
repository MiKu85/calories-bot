# Future Improvements

Ideas and features consciously postponed after MVP launch. Prioritize based on user feedback and growth metrics.

## Monetization
- Paid subscription tiers (Telegram Stars or external payment)
- Trial period logic
- Premium features gate

## Analytics
- Weekly summary: calories, macros, trends, consistency score
- Monthly report with charts
- Personal records and streaks
- Export to Google Sheets or CSV

## UX and meal input
- Ingredient-level correction UI (edit individual items in a meal)
- Better portion correction flow (e.g., "actually it was 200g, not 150g")
- Barcode scanning support
- Integration with food databases (USDA, OpenFoodFacts) for precise values
- Save favorite meals for quick re-logging
- Meal templates / presets

## Personalization
- Food preferences and dislikes
- Allergy and intolerance tracking
- Dietary patterns (vegetarian, vegan, keto, etc.)
- Target weight and desired pace
- Micronutrient tracking (sodium, fiber, vitamins)

## Photo analysis improvements
- Better portion estimation via reference objects
- Multi-photo support for one meal
- Confidence threshold tuning and feedback loop
- Fine-tuned model for Russian cuisine

## Notifications and engagement
- Daily reminders to log meals
- Weekly check-in messages
- Streak notifications
- Goal milestone celebrations

## Social and referral
- Referral program
- Invite friends flow
- Leaderboard or challenges (optional, gamification)

## Admin and operations
- Web admin panel with charts and filters
- Bulk messaging to user segments
- A/B test framework for onboarding variants
- Error alerting via Telegram or Sentry
- Advanced retention and funnel analytics

## Infrastructure
- Timezone-aware daily tracking
- Multi-language support (English first after Russian)
- Redis for session/state caching
- Background task queue (Celery or arq) for heavy AI jobs
- Horizontal scaling support
