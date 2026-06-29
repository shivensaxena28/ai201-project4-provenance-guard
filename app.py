"""
app.py — Provenance Guard HTTP API.

Endpoints
  POST /submit   classify a piece of text; returns attribution, confidence,
                 and the transparency label. Rate limited.
  POST /appeal   contest a classification by content_id; flips status to
                 'under_review' and logs the appeal.
  GET  /log      most recent structured audit-log entries (documentation /
                 grading visibility; would require auth in production).
  GET  /health   liveness check.

Submission flow:
  text -> Signal 1 (LLM) + Signal 2 (stylometry) -> confidence scoring
       -> transparency label -> audit log -> JSON response
Appeal flow:
  content_id + reasoning -> status='under_review' -> audit log -> confirmation
"""

import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import storage
import detection
from labels import make_label

load_dotenv()

app = Flask(__name__)
storage.init_db()

# In-memory storage is fine for a single-process dev/grading setup.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.errorhandler(429)
def ratelimit_handler(e):
    return (
        jsonify(
            {
                "error": "rate_limit_exceeded",
                "message": "Too many submissions. Please slow down and try again shortly.",
                "detail": str(e.description),
            }
        ),
        429,
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    creator_id = (data.get("creator_id") or "").strip()

    if not text:
        return jsonify({"error": "missing_field", "message": "'text' is required"}), 400
    if not creator_id:
        return jsonify({"error": "missing_field", "message": "'creator_id' is required"}), 400

    result = detection.classify(text)
    label = make_label(result["attribution"], result["confidence"])

    content_id = str(uuid.uuid4())
    timestamp = storage.now_iso()

    record = {
        "content_id": content_id,
        "creator_id": creator_id,
        "text": text,
        "attribution": result["attribution"],
        "ai_score": result["ai_score"],
        "confidence": result["confidence"],
        "llm_score": result["llm_score"],
        "stylo_score": result["stylo_score"],
        "status": "classified",
        "timestamp": timestamp,
        "signals": result["signals"],
    }
    storage.record_classification(record)

    return jsonify(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "attribution": result["attribution"],
            "confidence": result["confidence"],
            "ai_score": result["ai_score"],
            "signals": {
                "llm_score": result["llm_score"],
                "stylo_score": result["stylo_score"],
                "breakdown": result["signals"],
            },
            "label": label,
            "status": "classified",
            "timestamp": timestamp,
        }
    )


@app.route("/appeal", methods=["POST"])
@limiter.limit("20 per hour")
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = (data.get("content_id") or "").strip()
    reasoning = (data.get("creator_reasoning") or "").strip()

    if not content_id:
        return jsonify({"error": "missing_field", "message": "'content_id' is required"}), 400
    if not reasoning:
        return (
            jsonify({"error": "missing_field", "message": "'creator_reasoning' is required"}),
            400,
        )

    updated = storage.record_appeal(content_id, reasoning)
    if updated is None:
        return jsonify({"error": "not_found", "message": "Unknown content_id"}), 404

    return jsonify(
        {
            "message": "Appeal received. This content is now under review by a human.",
            "content_id": content_id,
            "status": "under_review",
            "original_attribution": updated["attribution"],
            "original_confidence": updated["confidence"],
            "appeal_reasoning": reasoning,
            "appealed_at": updated["appealed_at"],
        }
    )


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({"entries": storage.get_log(limit=limit)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
