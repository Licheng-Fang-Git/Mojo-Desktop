import json
import threading
from datetime import datetime, timezone
from groq import Groq
from PyQt6.QtCore import QObject, pyqtSignal

LOG_PATH = "decisions.log"


class EvaluationBridge(QObject):
    """Same cross-thread pattern as AlertBridge in bridge.py — the worker
    thread emits this signal instead of touching GUI widgets directly."""

    evaluation_done = pyqtSignal(str, str, dict)  # site, reason, decision


def evaluate_reason(site: str, reason: str) -> dict:
    """
    YOUR TURN.

    Call the Groq API to decide whether `reason` justifies visiting `site`,
    then return a dict shaped like:

        {"decision": "allow" | "deny", "response": "<message to show the user>"}

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
    """
    raise NotImplementedError


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
