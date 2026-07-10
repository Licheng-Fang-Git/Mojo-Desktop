import json
import re
import threading
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from groq import Groq
from PyQt6.QtCore import QObject, pyqtSignal

load_dotenv()

LOG_PATH = "decisions.log"

# Repeat-offense escalation thresholds — see check_repeat_offense().
REPEAT_VISIT_WINDOW = timedelta(hours=1)
REPEAT_VISIT_LIMIT = 2  # 3rd+ attempt on the same site within the window triggers

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


def _load_site_history(site: str) -> list[dict]:
    """Every logged entry (full fields, including timestamp) for this site,
    oldest first. Used by check_repeat_offense, which needs more than
    get_recent_reasons' trimmed reason/decision pairs."""
    try:
        lines = open(LOG_PATH).read().splitlines()
    except FileNotFoundError:
        return []

    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("site") == site:
            entries.append(entry)
    return entries


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation so trivially different phrasings of
    the same excuse ("Bored!" vs "bored") compare equal. This is a simple
    deterministic check, not semantic understanding — a paraphrased excuse
    ("nothing else to do") won't be caught. That's the accepted tradeoff for
    catching repeats without an extra LLM call."""
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def check_repeat_offense(site: str, reason: str) -> dict | None:
    """
    Deterministic, LLM-free escalation check, run before evaluate_reason
    calls the API at all. Returns a forced "deny" decision dict if this
    counts as a repeat offense, or None if normal evaluation should proceed.

    Triggers on either:
    - This is the 3rd+ attempt (allowed or denied both count) on this site
      within REPEAT_VISIT_WINDOW, or
    - The normalized reason matches a reason already given for this site,
      at any point in the past.
    """
    history = _load_site_history(site)
    now = datetime.now(timezone.utc)

    recent = []
    for entry in history:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - ts <= REPEAT_VISIT_WINDOW:
            recent.append(entry)

    if len(recent) >= REPEAT_VISIT_LIMIT:
        return {
            "decision": "deny",
            "duration": 0,
            "response": (
                f"This is attempt #{len(recent) + 1} on {site} in the last hour. "
                "I already told you where I stand — the answer hasn't changed."
            ),
        }

    normalized_reason = _normalize(reason)
    if normalized_reason and any(_normalize(e.get("reason")) == normalized_reason for e in history):
        return {
            "decision": "deny",
            "duration": 0,
            "response": (
                f"\"{reason}\" — you already used that exact excuse for {site} before. "
                "Recycling a lie doesn't make it true. Denied."
            ),
        }

    return None


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
    forced = check_repeat_offense(site, reason)
    if forced is not None:
        return forced

    try:
        system_prompt = (
            "You are Mojo, a savage, cynical, and hyper-strict accountability partner. "
            "The user is a chronic procrastinator trying to sneak onto a distracting website. "
            "Your default stance is absolute suspicion. You MUST assume every reason is a lie or a cop-out.\n\n"
            "Respond with ONLY a JSON object containing exactly these three keys in this exact order:\n\n"
            "1. \"response\": 1-2 sentences spoken directly to the user. Be direct, call out their weakness, "
            "and explain why their excuse isn't good enough. Do not be polite.\n"
            "2. \"decision\": exactly one of \"allow\" or \"deny\". Eliminate 'maybe' entirely.\n"
            "   - \"deny\": The default choice. Use this if the reason is vague (e.g., 'research', 'checking something', "
            "'just quick'), emotional ('I'm tired', 'bored'), or lacks a concrete, immediate work-related sub-task.\n"
            "   - \"allow\": ONLY use this if they provide an undeniable, highly specific business/academic emergency "
            "or a tightly defined task that absolutely mandates this exact website (e.g., 'Need to grab the API documentation link posted on the team Twitter handle').\n"
            "3. \"duration\": an integer number of minutes. If \"decision\" is \"deny\", this MUST be 0. If "
            "\"decision\" is \"allow\", pick a tight, realistic number of minutes for the specific task described "
            "(e.g. 5 for grabbing a single link, 15 for a focused lookup) — never more than 30, and never "
            "open-ended.\n\n"
            "Example of the exact shape expected:\n"
            "{\"response\": \"'Quick break' is how your 2-hour dopamine spirals always start. Access denied. "
            "Get back to work.\", \"decision\": \"deny\", \"duration\": 0}"
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

        # Don't trust the model's duration blindly — clamp to a sane range
        # rather than letting a malformed/missing value grant an unbounded
        # or negative allowance.
        try:
            decision["duration"] = max(0, min(30, int(decision.get("duration", 0))))
        except (TypeError, ValueError):
            decision["duration"] = 0

        return decision

    except Exception as e:
        print(f"[Mojo] Error occurred while evaluating reason for {site}: {e}")
        return {
            "decision": "allow",
            "duration": 5,
            "response": "Unable to evaluate — defaulting to a short allow.",
        }


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
        "duration": decision.get("duration"),
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
