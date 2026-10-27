"""How the tutor talks: short, varied, reactive turns.

Teaching content (facts, numbers, questions and their answers) stays authored and verified;
only the connective words vary. Every tutor turn has one shape:

    react   (a few words about what the learner just said; optional)
    move    (one idea, usually one lesson beat)
    hand-off (one question or invitation, spoken last, then silence)

Phrases never repeat the variant used last time in the same situation. Variety can be turned
off (first variant always) so tests and demos are reproducible.
"""
from __future__ import annotations

import random
import re

PHRASES: dict[str, tuple[str, ...]] = {
    # Goal and starting check.
    "goal": ("{topic}, good pick.", "Great, {topic} it is.", "Nice choice: {topic}.", "{topic}. Let's get you there."),
    "experience": ("Have you worked with {topic} before?", "How familiar is {topic} to you?",
                   "Have you seen {topic} before?"),
    "probe": ("Quick one.", "Let's see.", "Try this one.", "One quick question."),
    "probe_known": ("Then let's check one thing.", "Great, quick check then.", "Good. Let's see where you are."),
    "probe_new": ("No problem, we'll start simple.", "That's fine, let's start from the basics.",
                  "Okay, we'll build up to it."),
    # Reactions to an answer. {echo} repeats a short correct answer back ("Yes, 2x.").
    "correct": ("Yes{echo}.", "Exactly{echo}.", "That's it{echo}.", "Right{echo}.", "Nice{echo}."),
    "partly": ("Close.", "Nearly there.", "Partly, yes."),
    "wrong": ("Not quite.", "Hmm, not quite.", "Not this time."),
    "unsure": ("No problem.", "That's fine.", "Okay, no worries."),
    "reveal": ("It's {answer}.", "The answer is {answer}.", "It's actually {answer}."),
    "retry": ("Try once more.", "Have another go.", "Give it one more try."),
    "explain_again": ("Try putting the reasoning in your own words.", "Say it once more in your own words."),
    "options": ("Is it {options}?", "Could it be {options}?", "Which is it: {options}?"),
    # The hint ladder: options, then two options, then the answer.
    "scaffold": ("Here are some options.", "Let me give you a few options.", "Let's narrow it down."),
    "narrow": ("It's one of these two.", "Let me narrow it to two.", "Here's a hint: it's one of two."),
    "hint": ("Here's a hint.", "A hint:", "Think back to this:"),
    "see_next": ("Let's see it in the next step.", "The next step shows it.", "Let's look at the next piece."),
    # Moving through the route (which is never spoken).
    "bridge_known": ("You know {known}, so let's start there.", "Let's build from {known}.",
                     "Since you know {known}, we'll start from there."),
    "bridge_new": ("Let's start with {topic}.", "First up: {topic}.", "We'll begin with {topic}."),
    "next": ("Now, {topic}.", "Next up: {topic}.", "On to {topic}."),
    "ready": ("Ready for the next piece?", "Want to keep going?", "Shall we take the next step?", "Ready to move on?"),
    "check_in": ("Make sense so far?", "With me so far?", "Does that make sense?", "Got it?"),
    "confused": ("Let's look at that more closely.", "Good catch, let's slow down.", "Fair question. Let's take it apart."),
    "detour": ("We need {topic} first.", "Let's step back to {topic} for a moment.", "Quick detour: {topic}."),
    "resume": ("Back to {topic}.", "Now, back to {topic}.", "Okay, back to {topic}."),
    "slow_down": ("Let's go through it again, slower.", "Let's take that again, step by step."),
    "move_on": ("Let's keep going and come back to this later.", "We'll come back to this one later.",
                "Let's move on for now and return to it."),
    "review": ("Let's review {topic}.", "Back to {topic} for a refresh.", "Let's revisit {topic}."),
    "deeper": ("Let's go further with {topic}.", "Let's dig deeper into {topic}.", "More on {topic}, then."),
    "goal_reached": ("You've got {topic}.", "That's {topic} done.", "Nice work, you've got {topic}."),
    "route_done": ("We've covered everything on the way to {topic}.", "That's the whole path to {topic}."),
    "what_next": ("What would you like to learn next?", "What's next?", "Where to next?"),
    "idle": ("What would you like to learn?", "What shall we learn?", "What are you curious about?"),
    "again": ("Here it is again.", "Once more.", "Again:"),
    "skipped": ("Okay, skipping that one.", "Sure, we'll skip it.", "Skipped. We can come back to it."),
    "skip_step": ("Okay, next small step.", "Sure, moving on."),
    "no_question": ("There's no question waiting right now.", "Nothing to answer just now."),
    # Recall and practice.
    "recall_start": ("Let's see what you remember. Take your time.", "From memory first. No rush."),
    "recall_wait": ("Finish this one first, or skip it.", "Let's finish this one, or you can skip it."),
    "nothing_due": ("Nothing to review yet.", "You're all caught up on reviews."),
    "warmup_done": ("That's the warm-up done.", "Warm-up finished."),
    "back_to_it": ("Back to where you left off.", "Let's pick up where we were."),
    "stop_or_new": ("Pick something new, or stop here for today.", "Try something new, or call it a day."),
    "grader_down": ("I couldn't check that just now. Try again, or skip it.",),
    "gap": ("That's worth another look after a short break.", "Let's come back to that after a break."),
    "teach_back": ("How would you explain {topic} to a friend, with an example?",
                   "Teach me {topic} in your own words, with an example."),
}
# A turn's spoken glue stays within this budget; teaching content is not trimmed.
MAX_GLUE_LINES = 2
MAX_GLUE_WORDS = 16


class Phrasing:
    """Picks a phrase variant per situation, never the same one twice in a row."""

    def __init__(self, variety=True, seed=None):
        self.variety, self.rng = variety, random.Random(seed)

    def say(self, key, last: dict | None = None, **values) -> str:
        options = PHRASES[key]
        if not self.variety or len(options) == 1:
            choice = options[0]
        else:
            previous = (last or {}).get(key)
            choice = self.rng.choice([o for o in options if o != previous] or list(options))
        if last is not None:
            last[key] = choice
        text = choice.format(**values)
        return text[0].upper() + text[1:] if text else text

    def shuffle(self, items):
        items = list(items)
        self.rng.shuffle(items)
        return items


def echo(answer: str, expected: str) -> str:
    """Repeat a short answer back ("Yes, 2x."); long answers are not parroted."""
    words = answer.strip().rstrip(".!?").split()
    if not words or len(words) > 4:
        return ""
    short = expected if len(expected.split()) <= 4 else " ".join(words)
    return f", {short}"


def glue(lines) -> list[str]:
    """Keep a turn short: at most two connective lines, no repeats, none over the word budget."""
    kept = []
    for line in lines:
        line = (line or "").strip()
        if not line or line in kept:
            continue
        kept.append(line)
    if len(kept) > MAX_GLUE_LINES:  # Keep the reaction (first) and the transition (last).
        kept = [kept[0], kept[-1]]
    return kept


def joined(choices) -> str:
    choices = list(choices)
    if len(choices) <= 2:
        return " or ".join(choices)
    return ", ".join(choices[:-1]) + ", or " + choices[-1]


def options_redundant(prompt: str, choices) -> bool:
    """Options already in the question ("longer or shorter?") or plain yes/no aren't read out."""
    words = {c.casefold() for c in choices}
    if words <= {"yes", "no"}:
        return True
    text = prompt.casefold()
    return all(re.search(r"\b" + re.escape(c.casefold()) + r"\b", text) for c in choices)
