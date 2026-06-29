"""
detection.py — The multi-signal attribution pipeline.

Two genuinely independent signals:

  Signal 1 — LLM classification (Groq, llama-3.3-70b-versatile)
      Asks a large model to holistically judge whether the text reads as
      human- or AI-written. Captures *semantic / stylistic coherence* — the
      things that are hard to reduce to a formula.
      Output: ai_probability in [0, 1] (1 = almost certainly AI).
      Blind spot: lightly human-edited AI text; non-native human writing that
      "feels" generated; and the model's own biases. It is a black box.

  Signal 2 — Stylometric heuristics (pure Python)
      Measurable *structural* properties of the prose. AI text tends to be
      uniform, formal, and transition-heavy; human writing is burstier and
      more informal. Output: ai_likelihood in [0, 1].
      Blind spot: short texts (not enough sentences for variance to mean
      anything) and deliberately formal human writing (academic prose).

The two are combined into a single calibrated confidence. They are independent
because one is semantic and one is structural, so agreement between them is
real corroboration and disagreement is real uncertainty.
"""

import os
import re
import json
import statistics

# ---------------------------------------------------------------------------
# Tunable constants (documented in planning.md / README.md)
# ---------------------------------------------------------------------------

LLM_WEIGHT = 0.65          # the LLM is the more reliable holistic judge
STYLO_WEIGHT = 0.35        # stylometry corroborates and catches model blind spots

AI_THRESHOLD = 0.75        # ai_score must clear this to be called "likely_ai".
                           # Deliberately high (vs. the 0.35 human bar) so the
                           # "uncertain" band 0.35-0.75 is tilted away from
                           # accusing a human's work of being AI — a false
                           # positive is the worst outcome on a writing platform.
HUMAN_THRESHOLD = 0.35     # ai_score must fall below this to be "likely_human"
DISAGREEMENT_LIMIT = 0.45  # if the two signals disagree by more than this,
                           # we refuse to commit and return "uncertain"

GROQ_MODEL = "llama-3.3-70b-versatile"


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# Signal 2: Stylometric heuristics
# ---------------------------------------------------------------------------

# Discourse markers that AI assistants over-use relative to casual human prose.
TRANSITION_MARKERS = [
    "furthermore", "moreover", "additionally", "consequently", "nevertheless",
    "in conclusion", "it is important to note", "it is worth noting",
    "in addition", "as a result", "on the other hand", "in summary",
    "overall", "ultimately", "notably", "importantly", "in essence",
    "that being said", "it is essential", "plays a crucial role",
]

# Casual / informal markers that point toward an unedited human voice.
INFORMAL_MARKERS = [
    "honestly", "tbh", "lol", "lmao", "kinda", "sorta", "gonna", "wanna",
    "ok so", "i mean", "anyway", "yeah", "nah", "stuff", "the thing is",
    "like,", "you know", "btw", "ngl", "probably", "idk",
]


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _words(text):
    return re.findall(r"[a-zA-Z']+", text.lower())


def stylometric_signal(text):
    """Return (ai_likelihood, metrics) where ai_likelihood is in [0, 1] and
    higher means *more* AI-like. `metrics` is a dict of the interpretable
    sub-scores so the audit log and README can show the breakdown.
    """
    sentences = _sentences(text)
    words = _words(text)

    if len(words) < 5:
        # Too short to say anything meaningful — sit exactly on the fence.
        return 0.5, {"note": "text too short for reliable stylometry"}

    # --- Sub-score 1: sentence-length uniformity (burstiness) ----------------
    # Humans vary sentence length a lot; AI is metronomic. We use the
    # coefficient of variation (std / mean). Low CV  ->  AI-like.
    lengths = [len(_words(s)) for s in sentences] or [len(words)]
    if len(lengths) >= 2 and statistics.mean(lengths) > 0:
        cv = statistics.pstdev(lengths) / statistics.mean(lengths)
        uniformity = _clamp(1 - cv / 0.6)   # cv>=0.6 (very bursty) -> 0
    else:
        uniformity = 0.5

    # --- Sub-score 2: formality via long-word ratio --------------------------
    # AI / formal writing leans on long, latinate words.
    long_words = sum(1 for w in words if len(w) >= 7)
    long_ratio = long_words / len(words)
    formality = _clamp(long_ratio / 0.25)   # >=25% long words -> fully "formal"

    # --- Sub-score 3: transition-marker density ------------------------------
    lower = " " + text.lower() + " "
    transition_hits = sum(lower.count(m) for m in TRANSITION_MARKERS)
    transition_density = transition_hits / max(len(sentences), 1)
    transition = _clamp(transition_density / 0.5)  # 1 marker / 2 sentences -> 1

    # --- Sub-score 4: informality (human marker, inverted) -------------------
    informal_hits = sum(lower.count(m) for m in INFORMAL_MARKERS)
    # standalone lowercase "i" is a strong casual-typing tell
    informal_hits += len(re.findall(r"(?<![A-Za-z])i(?![A-Za-z'])", text))
    # ALL-CAPS emphasis words (WAY, SO) read as human
    informal_hits += len(re.findall(r"\b[A-Z]{2,}\b", text))
    human_informality = _clamp(informal_hits / 3.0)
    ai_informality = 1 - human_informality

    ai_likelihood = round(
        0.30 * uniformity
        + 0.20 * formality
        + 0.25 * transition
        + 0.25 * ai_informality,
        4,
    )

    metrics = {
        "sentence_count": len(sentences),
        "uniformity": round(uniformity, 3),
        "formality_long_word_ratio": round(formality, 3),
        "transition_density": round(transition, 3),
        "ai_informality": round(ai_informality, 3),
    }
    return ai_likelihood, metrics


# ---------------------------------------------------------------------------
# Signal 1: LLM classification (Groq)
# ---------------------------------------------------------------------------

_LLM_PROMPT = """You are an AI-content detector for a creative-writing platform. \
Assess the probability that the following text was generated by an AI language \
model (rather than written by a human).

Weigh holistic, semantic cues: generic phrasing, even tone, lack of lived \
specificity, hedging, and "essay-like" coherence point toward AI. Idiosyncrasy, \
concrete personal detail, irregular voice, and genuine messiness point toward \
human. A non-native or formal human writer is still HUMAN — do not penalize \
formality alone.

Respond with ONLY a JSON object of the form:
{"ai_probability": <float 0..1>, "reasoning": "<one short sentence>"}

TEXT:
\"\"\"
%s
\"\"\""""


def llm_signal(text):
    """Return (ai_probability, info). On any failure (no key, network, parse)
    we degrade gracefully to a neutral 0.5 and flag it, so the system stays
    runnable for grading even without a live API.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return 0.5, {"available": False, "reason": "GROQ_API_KEY not set"}

    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": _LLM_PROMPT % text}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)
        prob = _clamp(float(data.get("ai_probability", 0.5)))
        return round(prob, 4), {
            "available": True,
            "reasoning": data.get("reasoning", ""),
            "model": GROQ_MODEL,
        }
    except Exception as exc:  # network, rate-limit, bad JSON, etc.
        return 0.5, {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Combination + calibration
# ---------------------------------------------------------------------------

def classify(text):
    """Run both signals, combine them, and return the full attribution result.

    Returns a dict with: attribution, ai_score, confidence, llm_score,
    stylo_score, and a nested `signals` breakdown for the audit log.
    """
    llm_score, llm_info = llm_signal(text)
    stylo_score, stylo_metrics = stylometric_signal(text)

    llm_available = llm_info.get("available", False)

    if llm_available:
        ai_score = LLM_WEIGHT * llm_score + STYLO_WEIGHT * stylo_score
    else:
        # No live LLM judgement: lean entirely on stylometry but stay humble.
        ai_score = stylo_score
    ai_score = round(ai_score, 4)

    disagreement = abs(llm_score - stylo_score)

    # Directional certainty: how far from a coin-flip is the leading class.
    directional = max(ai_score, 1 - ai_score)
    # Disagreement between independent signals erodes confidence; so does a
    # missing LLM signal.
    confidence = directional * (1 - 0.25 * disagreement)
    if not llm_available:
        confidence *= 0.85
    confidence = round(_clamp(confidence), 4)

    # --- Verdict (asymmetric, biased against false-positives) ----------------
    if llm_available and disagreement > DISAGREEMENT_LIMIT:
        attribution = "uncertain"
    elif ai_score >= AI_THRESHOLD:
        attribution = "likely_ai"
    elif ai_score <= HUMAN_THRESHOLD:
        attribution = "likely_human"
    else:
        attribution = "uncertain"

    return {
        "attribution": attribution,
        "ai_score": ai_score,
        "confidence": confidence,
        "llm_score": llm_score,
        "stylo_score": stylo_score,
        "signals": {
            "llm": {"score": llm_score, **llm_info},
            "stylometry": {"score": stylo_score, **stylo_metrics},
            "weights": {"llm": LLM_WEIGHT, "stylometry": STYLO_WEIGHT},
            "disagreement": round(disagreement, 4),
        },
    }
