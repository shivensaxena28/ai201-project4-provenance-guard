"""
test_pipeline.py — Calibration harness (Milestone 4).

Runs the four deliberately chosen spec inputs through the full pipeline and
prints each signal separately plus the combined verdict. Use this to confirm
that scores vary meaningfully and that the three label categories are all
reachable.

Run:  python test_pipeline.py
(Works without a GROQ_API_KEY — the LLM signal degrades to neutral 0.5 and the
verdict falls back to stylometry, which is enough to see the structure. With a
key set, you get the full multi-signal result.)
"""

from dotenv import load_dotenv

import detection
from labels import make_label

load_dotenv()

CASES = {
    "clearly AI-generated": (
        "Artificial intelligence represents a transformative paradigm shift in "
        "modern society. It is important to note that while the benefits of AI "
        "are numerous, it is equally essential to consider the ethical "
        "implications. Furthermore, stakeholders across various sectors must "
        "collaborate to ensure responsible deployment."
    ),
    "clearly human-written": (
        "ok so i finally tried that new ramen place downtown and honestly? "
        "underwhelming. the broth was fine but they put WAY too much sodium in "
        "it and i was thirsty for like three hours after. my friend got the "
        "spicy version and said it was better. probably won't go back unless "
        "someone drags me there"
    ),
    "borderline: formal human": (
        "The relationship between monetary policy and asset price inflation has "
        "been extensively studied in the literature. Central banks face a "
        "fundamental tension between their mandate for price stability and the "
        "unintended consequences of prolonged low interest rates on equity and "
        "real estate valuations."
    ),
    "borderline: lightly edited AI": (
        "I've been thinking a lot about remote work lately. There are genuine "
        "tradeoffs — flexibility and no commute on one side, isolation and "
        "blurred work-life boundaries on the other. Studies show productivity "
        "varies widely by individual and role type."
    ),
}


def main():
    for name, text in CASES.items():
        result = detection.classify(text)
        label = make_label(result["attribution"], result["confidence"])
        print("=" * 72)
        print(f"CASE: {name}")
        print(f"  llm_score   = {result['llm_score']}")
        print(f"  stylo_score = {result['stylo_score']}")
        print(f"  ai_score    = {result['ai_score']}")
        print(f"  confidence  = {result['confidence']}")
        print(f"  attribution = {result['attribution']}")
        print(f"  label       = {label['headline']}")
    print("=" * 72)


if __name__ == "__main__":
    main()
