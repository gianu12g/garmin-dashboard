# Garmin Dashboard

## Garmin Authentication

- This project uses `garminconnect` library (v0.3.3, requires Python 3.12+)
- Authentication uses a **single token file**: `garmin_tokens.json`
- Local path: `C:\Users\Gianu\.garminconnect\garmin_tokens.json`
- The `GARMINTOKENS` env var should point to the **directory** containing `garmin_tokens.json` (default: `~/.garminconnect`)
- **Do NOT use `garth`** — it is outdated. The current library handles token persistence via `garmin_tokens.json` directly.
- There are no `oauth1_token.json` / `oauth2_token.json` files — just the single `garmin_tokens.json`.

## Deployment (Render)

- Hosted on Render.com
- Env vars: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `ANTHROPIC_API_KEY`
- For token-based auth on Render: base64-encode `garmin_tokens.json` into a `GARMIN_TOKENS_B64` env var, and the app restores it on startup.

## Stack

- Backend: Flask (Python)
- Frontend: Vanilla JS + Chart.js + marked.js (single `index.html` template)
- AI analysis: Anthropic Claude (claude-sonnet-4-6)
- Data source: Garmin Connect API via `garminconnect` library

## Key API Response Structures

The raw `garminconnect` library returns Garmin's original deeply nested JSON. Key paths:

- **VO2max**: `mostRecentVO2Max.generic.vo2MaxValue`
- **Training status**: `mostRecentTrainingStatus.latestTrainingStatusData.<deviceId>.trainingStatusFeedbackPhrase`
- **Load (ACWR)**: `mostRecentTrainingStatus.latestTrainingStatusData.<deviceId>.acuteTrainingLoadDTO.dailyAcuteChronicWorkloadRatio`
- **Training balance**: `mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap.<deviceId>.trainingBalanceFeedbackPhrase`
- **HRV**: nested under `hrvSummary` (keys: `lastNightAvg`, `weeklyAvg`, `status`, `baseline.balancedLow`, etc.)
- **Endurance**: `overallScore` (int), `classification` (integer ID: 1=recreational, 2=intermediate, 3=trained, 4=well_trained, 5=expert, 6=superior, 7=elite)
- **Race predictions**: flat dict with `time5K`, `time10K`, `timeHalfMarathon`, `timeMarathon` (all in seconds)
