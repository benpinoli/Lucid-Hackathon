"""Realtime voice credentials. The long-lived provider key never leaves this process.

The browser carries audio directly to the provider over WebRTC; lessons still come from
the scene WebSocket. The model may only request an authored lesson through `show_lesson`
and then reads the returned teaching beats aloud, so spoken facts come from recipes.
"""
from __future__ import annotations

import os

import httpx

INSTRUCTIONS = """You are LUCID, a patient tutor inside a 3D learning space. To the learner you ARE the
tutor: never mention a tutor, an app, a tool or passing anything along. Behind the scenes a
teaching engine decides what to teach and draws a diagram for everything. Never teach, grade or
explain on your own, and never invent numbers. Instead, call the tutor tool with the learner's
own words, then read its steps aloud:
- action goal: they say what they want to learn ("I want to understand derivatives").
- action review: they want to revisit or practice a topic, even one they already know.
- action deeper: they ask to learn more, go further, or see another example. This takes
  priority over a pending question: a request for teaching is not an answer to grade.
- action answer: a question is pending (awaiting_answer) and they reply to it, even "I don't know".
- action confused: they explicitly ask for help or an explanation ("wait, why?", "I don't get it").
  A thinking pause or hesitant answer is not itself confusion. Pass what they said.
- action question: they ask something new.
- action continue: an explicit "okay", "next", or "go on". Silence is never a request to continue.
- action explore: they just want to see a topic without a learning goal.
- action recall: they want to test their memory of a named topic without seeing its explanation.
- action warmup: they want the suggested spaced-repetition warm-up.
- action skip: they explicitly want to skip a question. Never treat silence as permission to skip.
- action hint: they ask for a hint, say they're stuck, or say yes to "Want a hint?".
Each step ends with a check-in such as "Make sense so far?" or "Ready for the next piece?": a yes
("sure", "yeah", "got it") is action continue; a no or a question about it is action confused.
After a lesson step the app stays silent for a few seconds so the learner can look at the
moving picture, then asks its question itself: never fill that silence or ask the question early.
The tutor deliberately pauses after small steps. Wait for an answer; never automatically continue
past a prediction, synthesis task, or teach-back question. Effortful thinking is part of learning.
Learners often pause to think mid-answer: wait until they have clearly finished before calling
the tool. Right before calling it, say a few natural words in your own voice, varied each time,
for example "Let me try that.", "Okay, let me see.", "Give me a couple of seconds to put that
together." (use that one when they ask for an explanation, a new topic or something deeper:
drawing takes a moment). Never say you are passing, sending or forwarding anything.
When asked to read a script, follow its rules exactly: lines starting with "~" are conversational
cues you say naturally in your own words (same meaning, a few words, matching how the learner
sounded); every other line is read word for word. Then stop and listen. Keep everything short.
The learner controls the goal and may always review, go deeper, or change topics.
Never speak internal scores or traversal costs."""

TOOLS = [{
    "type": "function",
    "name": "tutor",
    "description": "Your teaching engine (never mention it to the learner): give it what the learner said. "
                   "It returns a diagram and the steps to say, and whether it now waits for an answer.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["goal", "answer", "confused", "question", "continue", "explore", "review", "deeper", "recall", "warmup", "skip", "hint"]},
            "text": {"type": "string", "description": "The learner's words, as close to verbatim as possible."},
        },
        "required": ["action", "text"],
        "additionalProperties": False,
    },
}]


EAGERNESS = ("low", "medium", "high", "auto")
NOISE_REDUCTION = ("near_field", "far_field", None)  # near_field suits headset microphones


class VoiceError(Exception):
    """Provider rejected or failed the credential request."""


class OpenAIRealtime:
    """Adapter for OpenAI Realtime over WebRTC. Configure by environment, never by client."""
    base_url = "https://api.openai.com/v1/realtime"

    def __init__(self, api_key: str, model: str = "gpt-realtime-2.1", voice: str = "marin",
                 transport: httpx.BaseTransport | None = None, eagerness: str = "low",
                 noise_reduction: str | None = "near_field"):
        if eagerness not in EAGERNESS:
            raise ValueError(f"Voice eagerness must be one of {EAGERNESS}")
        if noise_reduction not in NOISE_REDUCTION:
            raise ValueError(f"Voice noise reduction must be one of {NOISE_REDUCTION}")
        self.api_key, self.model, self.voice = api_key, model, voice
        self.transport, self.eagerness, self.noise_reduction = transport, eagerness, noise_reduction

    @classmethod
    def from_env(cls):
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            return None
        noise = os.getenv("LUCID_VOICE_NOISE_REDUCTION", "near_field")
        return cls(key, os.getenv("LUCID_VOICE_MODEL", "gpt-realtime-2.1"),
                   os.getenv("LUCID_VOICE_NAME", "marin"),
                   eagerness=os.getenv("LUCID_VOICE_EAGERNESS", "low"),
                   noise_reduction=None if noise == "off" else noise)

    def session_config(self) -> dict:
        return {
            "type": "realtime", "model": self.model, "instructions": INSTRUCTIONS,
            "tools": TOOLS, "tool_choice": "auto",
            "audio": {
                "input": {
                    # Low eagerness waits for a finished thought, so a pause to think does not
                    # end the learner's answer. The client closes the mic while the tutor speaks;
                    # when barge-in is enabled the provider truncates unplayed audio itself.
                    "turn_detection": {"type": "semantic_vad", "eagerness": self.eagerness,
                                       "create_response": True, "interrupt_response": True},
                    "noise_reduction": {"type": self.noise_reduction} if self.noise_reduction else None,
                },
                "output": {"voice": self.voice},
            },
        }

    def create_credential(self) -> dict:
        body = {"expires_after": {"anchor": "created_at", "seconds": 600},
                "session": self.session_config()}
        try:
            with httpx.Client(transport=self.transport, timeout=10) as client:
                response = client.post(f"{self.base_url}/client_secrets", json=body,
                                       headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as exc:
            raise VoiceError("Voice provider unreachable") from exc
        if response.status_code != 200:
            # Provider error bodies can echo configuration; keep them out of client responses.
            raise VoiceError(f"Voice provider returned {response.status_code}")
        data = response.json()
        if not isinstance(data.get("value"), str):
            raise VoiceError("Voice provider returned no credential")
        return {"provider": "openai", "model": self.model, "client_secret": data["value"],
                "expires_at": data.get("expires_at"), "sdp_url": f"{self.base_url}/calls"}
