"""ATMOS Flask application."""
import logging
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from config import DEBUG
from ml_insights import analyze
from monthly_mapper import compute_monthly_carbon_data, get_monthly_ranges

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("atmos")

app = Flask(__name__, template_folder="../templates", static_folder="../static")

MAX_MONTHS = 60


class BadRequest(Exception):
    pass


def _validate(payload) -> tuple[list, str, str]:
    if not isinstance(payload, dict):
        raise BadRequest("Request body must be a JSON object.")

    coords = payload.get("coords")
    start_date = payload.get("startDate")
    end_date = payload.get("endDate")

    if not coords or not isinstance(coords, list):
        raise BadRequest("'coords' must be a non-empty GeoJSON polygon ring list.")
    if not start_date or not end_date:
        raise BadRequest("Both 'startDate' and 'endDate' are required.")

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise BadRequest("Dates must be formatted as YYYY-MM-DD.")

    if start >= end:
        raise BadRequest("'startDate' must be earlier than 'endDate'.")
    if len(get_monthly_ranges(start_date, end_date)) > MAX_MONTHS:
        raise BadRequest(f"Date range too large; {MAX_MONTHS} months maximum.")

    return coords, start_date, end_date


@app.route("/")
def index():
    return render_template("map.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/get_data", methods=["POST"])
def get_data():
    try:
        coords, start_date, end_date = _validate(request.get_json(silent=True))
    except BadRequest as exc:
        return jsonify({"error": str(exc)}), 400

    log.info("Computing %s -> %s", start_date, end_date)
    try:
        result = compute_monthly_carbon_data(coords, start_date, end_date)
        log.info("Returned %d month(s)", len(result))
        return jsonify(result)
    except Exception as exc:
        log.exception("compute_monthly_carbon_data failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/analyze", methods=["POST"])
def analyze_route():
    """ML insights over monthly rows already returned by /get_data.

    Pure computation -- no Earth Engine calls -- so it is fast to re-run.
    """
    payload = request.get_json(silent=True)
    rows = payload.get("rows") if isinstance(payload, dict) else None

    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        return jsonify({"error": "'rows' must be a non-empty list of monthly records."}), 400
    if len(rows) > MAX_MONTHS:
        return jsonify({"error": f"At most {MAX_MONTHS} months can be analysed."}), 400

    try:
        return jsonify(analyze(rows))
    except Exception as exc:
        log.exception("analyze failed")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=DEBUG)
