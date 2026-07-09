import json
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv
from groq import Groq
from PyQt6.QtCore import QObject, pyqtSignal

load_dotenv()

LOG_PATH = "decisions.log"

client = Groq()


class EvaluationBridge(QObject):
    """Same cross-thread pattern as AlertBridge in bridge.py — the worker
    thread emits this signal instead of touching GUI widgets directly."""

    evaluation_done = pyqtSignal(str, str, dict)  # site, reason, decision


def get_recent_reasons(site: str, limit: int = 3) -> list[dict]:
    """
    YOUR TURN.

    Read LOG_PATH (same JSON-lines format log_decision writes) and return
    the most recent `limit` entries for this specific `site` — each entry
    being one of the dicts log_decision wrote (site/reason/decision/response
    /timestamp).

    Things to handle:
    - The file may not exist yet (first run ever) — don't crash.
    - Each line is a separate json.loads() call, not one big json.load().
    - You want the *last* `limit` matching entries, in chronological order
      (oldest of the three first) so the model reads them as a timeline,
      not scrambled.
    """
    try:
        lines = open(LOG_PATH).read().splitlines()
    except FileNotFoundError:
        return []

    matches = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("site") == site:
            matches.append({"reason": entry.get("reason"), "decision": entry.get("decision")})
    return matches[-limit:]


def evaluate_reason(site: str, reason: str) -> dict:
    """
    YOUR TURN.

    Call the Groq API to decide whether `reason` justifies visiting `site`,
    then return a dict shaped like:

        {"decision": "allow" | "deny" | "maybe", "response": "<message to show the user>"}

    Steps to work through:
    1. `pip install groq` is done — `from groq import Groq`, then
       `client = Groq()` (it reads GROQ_API_KEY from the environment
       automatically).
    2. Call `client.chat.completions.create(...)` with a `model` (e.g.
       "llama-3.1-8b-instant"), a system prompt that tells the model what
       job it's doing (acting as a strict-but-fair accountability partner,
       not a generic assistant), and the user's `reason` as the user
       message.
    3. Pass `response_format={"type": "json_object"}` and explicitly ask
       for JSON in your prompt too — that's what makes the model's reply
       parseable instead of free-form prose.
    4. Parse the response content with `json.loads` into the dict shape
       above.
    5. This function must never let an exception escape uncaught — if the
       API call fails or returns something unparseable, decide what the
       safe fallback decision should be (allow, or deny, and why).

    Once get_recent_reasons() is implemented, call it here and fold the
    result into user_message — e.g. a short "past reasons for this site:
    ..." block before the current reason — so the model can weigh whether
    this is a repeat excuse. Let the model do the summarizing/pattern-
    spotting; your job is just handing it the raw history.
    """
    try:
        system_prompt = (
            "You are Mojo, a strict-but-fair accountability partner. The user is trying to "
            "avoid distracting websites, and is asking permission to visit one. Your job is to "
            "judge whether their stated reason is genuine and specific, or a vague excuse.\n\n"
            "Respond with ONLY a JSON object, no text outside it, containing exactly these two "
            "keys in this exact order:\n\n"
            "1. \"response\": 1-2 sentences, spoken directly to the user, explaining your "
            "reasoning. Write this FIRST — decide what you think by explaining it, don't just "
            "state a verdict.\n"
            "2. \"decision\": exactly one of \"allow\", \"deny\", or \"maybe\":\n"
            "   - \"deny\": the reason is clearly a flimsy excuse, vague, or dishonest-sounding "
            "(e.g. \"just bored\", \"5 mins won't hurt\").\n"
            "   - \"allow\": the reason is clearly specific and legitimate (e.g. a concrete task "
            "that genuinely requires this site).\n"
            "   - \"maybe\": your default — the reason is plausible but not clearly justified "
            "either way. Only use \"deny\" when the reason is genuinely weak, not just uncertain.\n\n"
            "Example of the exact shape expected:\n"
            "{\"response\": \"You haven't said what for, and 'research' alone isn't specific "
            "enough to be sure this won't turn into scrolling.\", \"decision\": \"maybe\"}"
        )
        history = get_recent_reasons(site)
        if history:
            history_lines = "\n".join(
                f"- Reason: \"{h['reason']}\" -> Decision: {h['decision']}" for h in history
            )
            history_block = f"Past reasons the user has given for this site:\n{history_lines}\n\n"
        else:
            history_block = ""

        user_message = (
            f"{history_block}"
            f"Site: {site}\nReason: {reason}\nPlease respond in JSON format."
        )

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
        )

        # Assuming the model's response is in the expected JSON format.
        decision = json.loads(response.choices[0].message.content)
        return decision

    except Exception as e:
        print(f"[Mojo] Error occurred while evaluating reason for {site}: {e}")
        return {"decision": "allow", "response": "Unable to evaluate — defaulting to allow."}


def evaluate_async(site: str, reason: str, bridge: EvaluationBridge):
    def _run():
        decision = evaluate_reason(site, reason)
        bridge.evaluation_done.emit(site, reason, decision)

    threading.Thread(target=_run, daemon=True).start()


def log_decision(site: str, reason: str, decision: dict):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "site": site,
        "reason": reason,
        "decision": decision.get("decision"),
        "response": decision.get("response"),
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
