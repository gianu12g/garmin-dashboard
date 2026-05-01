import os
import gc
import json
import base64
import traceback
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify
from garminconnect import Garmin
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")
GARMIN_TOKENSTORE = os.environ.get("GARMINTOKENS", os.path.expanduser("~/.garminconnect"))

cached_data = None
cached_analysis = None
last_update = None


def _restore_tokens_from_env():
    """If GARMIN_TOKENS_B64 env var exists, decode it to garmin_tokens.json in the token store."""
    tokens_b64 = os.environ.get("GARMIN_TOKENS_B64", "")
    if tokens_b64:
        os.makedirs(GARMIN_TOKENSTORE, exist_ok=True)
        token_path = os.path.join(GARMIN_TOKENSTORE, "garmin_tokens.json")
        with open(token_path, "wb") as f:
            f.write(base64.b64decode(tokens_b64))


# Restore tokens on startup (for Render / cloud deploys)
_restore_tokens_from_env()


def get_garmin_client():
    """Login to Garmin Connect. Tries saved tokens first, falls back to email/password."""
    if GARMIN_EMAIL and GARMIN_PASSWORD:
        client = Garmin(email=GARMIN_EMAIL, password=GARMIN_PASSWORD)
    else:
        client = Garmin()
    client.login(GARMIN_TOKENSTORE)
    return client


def fetch_garmin_data():
    client = get_garmin_client()
    today = datetime.now().strftime("%Y-%m-%d")

    activities_raw = client.get_activities(0, 20)
    activities = []
    for a in activities_raw:
        act = {
            "name": a.get("activityName", ""),
            "type": a.get("activityType", {}).get("typeKey", "unknown"),
            "start_time": a.get("startTimeLocal", ""),
            "distance_meters": a.get("distance", 0) or 0,
            "duration_seconds": a.get("duration", 0) or 0,
            "moving_duration_seconds": a.get("movingDuration", 0) or 0,
            "calories": a.get("calories", 0) or 0,
            "avg_hr_bpm": a.get("averageHR", 0) or 0,
            "max_hr_bpm": a.get("maxHR", 0) or 0,
            "steps": a.get("steps", 0) or 0,
        }
        activities.append(act)
    del activities_raw
    gc.collect()

    hr_summary = client.get_heart_rates(today)
    hr_data = {
        "resting_hr": hr_summary.get("restingHeartRate", 0),
        "max_hr": hr_summary.get("maxHeartRate", 0),
        "min_hr": hr_summary.get("minHeartRate", 0),
    }

    sleep_raw = client.get_sleep_data(today)
    sleep_data = {}
    if sleep_raw and "dailySleepDTO" in sleep_raw:
        s = sleep_raw["dailySleepDTO"]
        sleep_seconds = s.get("sleepTimeSeconds", 0) or 0
        deep = s.get("deepSleepSeconds", 0) or 0
        light = s.get("lightSleepSeconds", 0) or 0
        rem = s.get("remSleepSeconds", 0) or 0
        awake = s.get("awakeSleepSeconds", 0) or 0
        total = deep + light + rem + awake if (deep + light + rem + awake) > 0 else 1
        sleep_data = {
            "sleep_seconds": sleep_seconds,
            "sleep_hours": round(sleep_seconds / 3600, 2),
            "sleep_score": s.get("sleepScores", {}).get("overall", {}).get("value", 0),
            "sleep_score_qualifier": s.get("sleepScores", {}).get("overall", {}).get("qualifierKey", ""),
            "deep_sleep_seconds": deep,
            "light_sleep_seconds": light,
            "rem_sleep_seconds": rem,
            "awake_seconds": awake,
            "deep_percent": round(deep / total * 100, 1),
            "light_percent": round(light / total * 100, 1),
            "rem_percent": round(rem / total * 100, 1),
        }

    training_status_raw = client.get_training_status(today)
    training_status = {}
    if training_status_raw:
        ts = training_status_raw[0] if isinstance(training_status_raw, list) else training_status_raw

        # VO2max: nested under mostRecentVO2Max.generic
        vo2_data = ts.get("mostRecentVO2Max", {}).get("generic", {})
        vo2 = vo2_data.get("vo2MaxValue") or vo2_data.get("vo2MaxPreciseValue") or 0
        if isinstance(vo2, float):
            vo2 = round(vo2, 1)

        # Training status & load: nested under mostRecentTrainingStatus.latestTrainingStatusData.<deviceId>
        ts_feedback = ""
        acute_load = 0
        chronic_load = 0
        load_ratio = 0
        latest_ts = ts.get("mostRecentTrainingStatus", {}).get("latestTrainingStatusData", {})
        for device_id, device_data in latest_ts.items():
            ts_feedback = device_data.get("trainingStatusFeedbackPhrase", "")
            atl = device_data.get("acuteTrainingLoadDTO", {})
            acute_load = atl.get("dailyTrainingLoadAcute", 0)
            chronic_load = atl.get("dailyTrainingLoadChronic", 0)
            load_ratio = round(atl.get("dailyAcuteChronicWorkloadRatio", 0) or 0, 2)
            break  # use first (primary) device

        # Training balance: nested under mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap.<deviceId>
        balance_feedback = ""
        balance_map = ts.get("mostRecentTrainingLoadBalance", {}).get("metricsTrainingLoadBalanceDTOMap", {})
        for device_id, device_data in balance_map.items():
            balance_feedback = device_data.get("trainingBalanceFeedbackPhrase", "")
            break

        training_status = {
            "vo2_max": vo2,
            "training_status_feedback": ts_feedback,
            "acute_load": acute_load,
            "chronic_load": chronic_load,
            "load_ratio": load_ratio,
            "training_balance_feedback": balance_feedback,
        }
    del training_status_raw
    gc.collect()

    training_readiness_raw = client.get_training_readiness(today)
    training_readiness = {}
    if training_readiness_raw:
        tr = training_readiness_raw[0] if isinstance(training_readiness_raw, list) else training_readiness_raw
        training_readiness = {
            "score": tr.get("score", 0),
            "level": tr.get("level", ""),
            "feedback": tr.get("feedback", ""),
            "sleep_factor_percent": tr.get("sleepScoreFactorPercent", 0),
            "recovery_factor_percent": tr.get("recoveryTimeFactorPercent", 0),
            "hrv_factor_percent": tr.get("hrvFactorPercent", 0),
            "hrv_weekly_avg": tr.get("hrvWeeklyAverage", 0),
            "training_load_factor_percent": tr.get("acuteTrainingLoadFactorPercent", 0),
        }

    hrv_raw = client.get_hrv_data(today)
    hrv_data = {}
    if hrv_raw:
        # HRV summary is nested under hrvSummary
        summary = hrv_raw.get("hrvSummary", {})
        baseline = summary.get("baseline", {})
        hrv_data = {
            "last_night_avg": summary.get("lastNightAvg", 0),
            "last_night_5min_high": summary.get("lastNight5MinHigh", 0),
            "weekly_avg": summary.get("weeklyAvg", 0),
            "status": summary.get("status", ""),
            "feedback": summary.get("feedbackPhrase", ""),
            "baseline_low": baseline.get("lowUpper", 0),
            "baseline_balanced_low": baseline.get("balancedLow", 0),
            "baseline_balanced_upper": baseline.get("balancedUpper", 0),
        }
    del hrv_raw
    gc.collect()

    try:
        # Single-day endpoint returns overallScore directly + classification as integer
        endurance_raw = client.get_endurance_score(today)
        score = endurance_raw.get("overallScore", 0)
        # classification is an integer ID, map to string key
        classification_id = endurance_raw.get("classification", 0)
        classification_map = {
            1: "recreational", 2: "intermediate", 3: "trained",
            4: "well_trained", 5: "expert", 6: "superior", 7: "elite",
        }
        classification = classification_map.get(classification_id, "unknown")
        endurance_data = {
            "current_score": score,
            "classification": classification,
        }
    except Exception as e:
        endurance_data = {"current_score": 0, "classification": "unknown"}

    def _format_race_time(secs):
        """Format seconds into M:SS or H:MM:SS."""
        if not secs:
            return None
        secs = int(secs)
        mins = secs // 60
        s = secs % 60
        if mins >= 60:
            h = mins // 60
            m = mins % 60
            return f"{h}:{m:02d}:{s:02d}"
        return f"{mins}:{s:02d}"

    try:
        race_raw = client.get_race_predictions()
        race_predictions = {}
        if race_raw and isinstance(race_raw, dict):
            # Flat dict with time5K, time10K, timeHalfMarathon, timeMarathon (seconds)
            race_keys = {
                "time5K": "5K",
                "time10K": "10K",
                "timeHalfMarathon": "Half Marathon",
                "timeMarathon": "Marathon",
            }
            for raw_key, display_name in race_keys.items():
                secs = race_raw.get(raw_key, 0)
                if secs:
                    race_predictions[display_name] = _format_race_time(secs)
    except Exception:
        race_predictions = {}

    try:
        pr_raw = client.get_personal_record()
        personal_records = []
        if pr_raw:
            for p in pr_raw:
                personal_records.append({
                    "type": p.get("typeKey", p.get("personalRecordType", "")),
                    "value": p.get("displayValue", str(p.get("value", ""))),
                })
    except Exception:
        personal_records = []

    running_activities = [a for a in activities if a["type"] == "running" and a["distance_meters"] > 1000]

    total_km_month = sum(a["distance_meters"] for a in running_activities if _is_this_month(a["start_time"])) / 1000
    runs_this_month = len([a for a in running_activities if _is_this_month(a["start_time"])])

    del activities
    gc.collect()

    return {
        "date": today,
        "running_activities": running_activities,
        "hr": hr_data,
        "sleep": sleep_data,
        "training_status": training_status,
        "training_readiness": training_readiness,
        "hrv": hrv_data,
        "endurance": endurance_data,
        "race_predictions": race_predictions,
        "personal_records": personal_records,
        "monthly_km": round(total_km_month, 1),
        "monthly_runs": runs_this_month,
    }


def _is_this_month(date_str):
    if not date_str:
        return False
    try:
        now = datetime.now()
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.year == now.year and dt.month == now.month
    except Exception:
        return False


def generate_ai_analysis(data):
    if not ANTHROPIC_API_KEY:
        return "API key de Anthropic no configurada. Configura ANTHROPIC_API_KEY en las variables de entorno."

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    runs = data.get("running_activities", [])[:20]
    runs_summary = []
    for r in runs:
        dist_km = round(r["distance_meters"] / 1000, 1)
        dur_min = round(r["moving_duration_seconds"] / 60, 1)
        pace_s = r["moving_duration_seconds"] / (r["distance_meters"] / 1000) if r["distance_meters"] > 0 else 0
        pace_min = int(pace_s // 60)
        pace_sec = int(pace_s % 60)
        runs_summary.append(f"  - {r['start_time'][:10]}: {dist_km}km, {dur_min}min, pace {pace_min}:{pace_sec:02d}/km, HR avg {r['avg_hr_bpm']}bpm")

    prompt = f"""Analiza los datos de rendimiento de este corredor y genera un perfil deportivo con recomendaciones.
Responde en español. Se conciso pero completo. Usa formato con secciones claras.

DATOS:
- VO2max: {data['training_status'].get('vo2_max', 'N/A')}
- FC reposo: {data['hr'].get('resting_hr', 'N/A')} bpm
- HRV semanal: {data['hrv'].get('weekly_avg', 'N/A')} ms (status: {data['hrv'].get('status', 'N/A')})
- Sleep score: {data['sleep'].get('sleep_score', 'N/A')} ({data['sleep'].get('sleep_hours', 'N/A')}h)
- Deep sleep: {data['sleep'].get('deep_percent', 'N/A')}%, REM: {data['sleep'].get('rem_percent', 'N/A')}%
- Training readiness: {data['training_readiness'].get('score', 'N/A')} ({data['training_readiness'].get('level', 'N/A')})
- Training status: {data['training_status'].get('training_status_feedback', 'N/A')}
- Load ratio (ACWR): {data['training_status'].get('load_ratio', 'N/A')}
- Training balance: {data['training_status'].get('training_balance_feedback', 'N/A')}
- Endurance score: {data['endurance'].get('current_score', 'N/A')} ({data['endurance'].get('classification', 'N/A')})
- Km este mes: {data.get('monthly_km', 'N/A')}
- Carreras este mes: {data.get('monthly_runs', 'N/A')}
- Race predictions: {json.dumps(data.get('race_predictions', {}))}

ULTIMAS CARRERAS:
{chr(10).join(runs_summary)}

Genera:
1. PERFIL DEL CORREDOR (nivel, fortalezas, tipo de corredor)
2. ANALISIS DE METRICAS (que esta bien, que preocupa)
3. RECOMENDACIONES (3-5 puntos concretos y accionables)
4. PROXIMA SEMANA (sugerencia de plan semanal)"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    global cached_data, last_update
    if cached_data and last_update:
        age = (datetime.now() - last_update).total_seconds()
        if age < 300:
            return jsonify({"data": cached_data, "analysis": cached_analysis, "last_update": last_update.isoformat()})

    try:
        cached_data = fetch_garmin_data()
        last_update = datetime.now()
        return jsonify({"data": cached_data, "analysis": cached_analysis, "last_update": last_update.isoformat()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/update", methods=["POST"])
def api_update():
    global cached_data, cached_analysis, last_update
    try:
        cached_data = fetch_garmin_data()
        last_update = datetime.now()

        try:
            cached_analysis = generate_ai_analysis(cached_data)
        except Exception as e:
            cached_analysis = f"Error generando analisis: {str(e)}"

        return jsonify({"data": cached_data, "analysis": cached_analysis, "last_update": last_update.isoformat()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug")
def api_debug():
    """Returns raw Garmin API responses to help debug field names."""
    try:
        client = get_garmin_client()
        today = datetime.now().strftime("%Y-%m-%d")

        raw = {}
        try:
            ts = client.get_training_status(today)
            raw["training_status"] = ts
        except Exception as e:
            raw["training_status_error"] = str(e)

        try:
            hrv = client.get_hrv_data(today)
            raw["hrv_data"] = hrv
        except Exception as e:
            raw["hrv_data_error"] = str(e)

        try:
            endurance = client.get_endurance_score(today)
            raw["endurance_score"] = endurance
        except Exception as e:
            raw["endurance_score_error"] = str(e)

        try:
            race = client.get_race_predictions()
            raw["race_predictions"] = race
        except Exception as e:
            raw["race_predictions_error"] = str(e)

        return jsonify(raw)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
