"""DeslopifyPolicy - Remove common AI filler phrases from LLM responses.

Strips sycophantic openers, hollow closers, and filler phrases that make
AI output feel generic. Cleans up leftover whitespace artifacts.

Uses TextModifierPolicy for full streaming + non-streaming support with
zero latency overhead (regex only, no LLM calls).

Example config:
    policy:
      class: "luthien_proxy.policies.deslopify_policy:DeslopifyPolicy"
      config: {}
"""

from __future__ import annotations

import re

from luthien_proxy.policy_core import TextModifierPolicy

# --- Pattern definitions ---
# Each pattern is compiled with IGNORECASE and MULTILINE.
# Patterns are applied in order: openers first, then closers, then inline filler,
# then whitespace cleanup last.

# Sycophantic openers: entire opening sentence that adds no information.
# These appear at the very start of a response (possibly after whitespace).
# Applied repeatedly until none match (so "Certainly! Great question!" both get stripped).
_OPENER_PATTERNS: list[str] = [
    # --- Sycophantic affirmations ---
    # "Great question!" / "That's a great question!" / "What a great question!"
    r"^(?:that['\u2019]?s\s+|what\s+)?(?:a\s+)?(?:really\s+)?(?:great|excellent|fantastic|wonderful|good|interesting|thoughtful|insightful|brilliant|clever|astute)\s+(?:question|point|observation|thought|insight|idea)[\s!.]*",
    # "Absolutely!" / "Certainly!" / "Of course!" / "Sure!" / "Sure thing!" / "Definitely!" / "Indeed!"
    r"^(?:absolutely|certainly|of\s+course|sure(?:\s+thing)?|definitely|indeed|exactly)[\s!.,]*",
    # --- Enthusiasm / willingness ---
    # "I'd be happy to help you understand how..." → strip up to "understand" since the
    # sentence was constructed as "help you understand <topic>" and keeping "you understand"
    # reads awkwardly. Must come before the general "happy to help" pattern.
    r"^I['\u2019]?(?:d|m)\s+(?:be\s+)?(?:happy|glad|love|delighted|pleased|excited)\s+to\s+(?:help|assist)\s+(?:you\s+)?(?:understand|explain)[\s!.]*",
    # "I'd be happy to help!" / "I'd love to help with that!" / "I'm happy to assist!"
    r"^I['\u2019]?(?:d|m)\s+(?:be\s+)?(?:happy|glad|love|delighted|pleased|excited)\s+to\s+(?:help|assist|explain|walk\s+you\s+through|break\s+(?:this|that|it)\s+down)(?:\s+(?:you\s+)?with\s+(?:this|that))?[\s!.]*",
    # --- Gratitude ---
    # "Thank you for asking about this!" / "Thanks for sharing" / "Thanks for the great question!"
    r"^(?:thank(?:s|\s+you))\s+(?:for\s+(?:the\s+)?(?:\w+\s+)*\w+)(?:\s+(?:about|regarding)\s+(?:this|that))?[\s!.]*",
    # --- Meta-commentary about the task ---
    # "Let me explain..." / "Let me walk you through..." / "Let me break this down..."
    r"^let\s+me\s+(?:explain|walk\s+you\s+through\s+(?:this|that|it)|break\s+(?:this|that|it)\s+down|help\s+(?:you\s+)?(?:with\s+(?:this|that)|understand)|clarify|elaborate|dive\s+(?:in|into\s+(?:this|that)))[\s!.,]*",
    # "Here's the thing:" / "Here's what you need to know:"
    r"^here['\u2019]?s\s+(?:the\s+thing|what\s+(?:you\s+(?:need|want)\s+to\s+know|I\s+(?:think|found|recommend)))[\s:.,!]*",
    # "So, " / "So basically, " / "Well, " (filler starters)
    r"^(?:so|well)(?:\s+basically)?,\s*",
    # "To answer your question, "
    r"^to\s+(?:answer|address)\s+your\s+question,?\s*",
    # "That's a really good/great/interesting ..." (when followed by more text)
    r"^that['\u2019]?s\s+(?:a\s+)?(?:really\s+)?(?:great|good|interesting|important|valid|fair)\s+(?:question|point|observation|concern)[.!,]?\s*",
]

# Hollow closers: trailing sentences that add nothing.
# These appear at the end of a response (possibly before whitespace).
_CLOSER_PATTERNS: list[str] = [
    # NOTE: patterns use \s+ (not \.?\s*) to avoid eating the preceding period.
    # The closer loop in deslopify() strips trailing whitespace after each pass.
    # --- Hope/help closers ---
    # "I hope this helps!" / "Hope that helps!" / "I hope this was helpful!"
    r"(?:^|\s+)I?\s*hope\s+(?:this|that|it)\s+(?:helps|was\s+helpful|is\s+helpful|clarifies\s+things|makes\s+sense)[\s!.]*$",
    # "I hope that clarifies things for you!"
    r"(?:^|\s+)I?\s*hope\s+(?:this|that)\s+(?:clarifies|answers|addresses|resolves).*$",
    # --- "Feel free" / "Don't hesitate" ---
    r"(?:^|\s+)(?:please\s+)?(?:feel\s+free|don['\u2019]?t\s+hesitate)\s+to\s+(?:ask|reach\s+out|let\s+me\s+know|contact).*$",
    # --- "Let me know" ---
    r"(?:^|\s+)let\s+me\s+know\s+if\s+(?:you\s+)?(?:have|need|want|there['\u2019]?s|this|that).*$",
    # --- "If you have any questions" ---
    r"(?:^|\s+)if\s+you\s+(?:have\s+)?(?:any\s+)?(?:other\s+|more\s+|further\s+)?(?:questions|doubts|concerns).*$",
    # --- "Happy X-ing!" ---
    r"(?:^|\s+)happy\s+\w+ing[\s!.]*$",
    # --- Summary closers that add nothing ---
    # "I'm here if you need anything else!" / "I'm always here to help!"
    r"(?:^|\s+)I['\u2019]?m\s+(?:here|always\s+here|always\s+happy)\s+(?:if\s+you|to\s+help).*$",
    # "Good luck!" / "Best of luck!" / "Good luck with your project!"
    r"(?:^|\s+)(?:best\s+of\s+|good\s+)luck(?:\s+with)?.*$",
    # "Is there anything else I can help you with?"
    r"(?:^|\s+)is\s+there\s+(?:anything|something)\s+else\s+(?:I\s+can|you['\u2019]?d\s+like).*$",
]

# Inline filler: phrases mid-text that pad without meaning.
# These are replaced with empty string (surrounding text stays).
# When a filler phrase starts a sentence, the next word gets capitalized.
_INLINE_PATTERNS: list[str] = [
    # --- AI self-reference ---
    # "As an AI language model, " / "As a large language model, "
    r"[Aa]s\s+an?\s+(?:AI|artificial\s+intelligence|large)\s+(?:language\s+)?model,?\s*",
    # "As an AI, " / "As an AI assistant, "
    r"[Aa]s\s+an\s+AI(?:\s+assistant)?,?\s*",
    # --- Filler hedges that weaken the statement ---
    # "It's worth noting that" / "It's important to note that" / "It's important to mention that"
    r"[Ii]t(?:['\u2018\u2019]?s|\s+is)\s+(?:(?:worth|important)\s+(?:to\s+)?(?:not(?:e|ing)|mention(?:ing)?|point(?:ing)?\s+out))\s+that\s*",
    # "I should mention that" / "I should point out that" / "I should note that"
    r"I\s+should\s+(?:mention|point\s+out|note)\s+that\s*",
    # "It should be noted that" / "It must be noted that"
    r"[Ii]t\s+(?:should|must|needs\s+to)\s+be\s+(?:noted|mentioned|pointed\s+out)\s+that\s*",
    # "I want to emphasize that" / "I'd like to point out that"
    r"I(?:['\u2019]d\s+like|\s+want)\s+to\s+(?:emphasize|highlight|point\s+out|stress|mention|note)\s+that\s*",
    # --- Transition filler ---
    # "Essentially, " / "Basically, " / "Fundamentally, " (at sentence start)
    r"(?:^|(?<=\.\s))(?:essentially|basically|fundamentally),?\s*",
    # "In essence, " / "In other words, " / "Put simply, "
    r"(?:^|(?<=\.\s))(?:in\s+essence|in\s+other\s+words|put\s+simply|simply\s+put|to\s+put\s+it\s+simply),?\s*",
    # "At the end of the day, " / "When all is said and done, "
    r"(?:^|(?<=\.\s))(?:at\s+the\s+end\s+of\s+the\s+day|when\s+all\s+is\s+said\s+and\s+done),?\s*",
    # "The key takeaway is that" / "The main thing to understand is that"
    r"(?:^|(?<=\.\s))the\s+(?:key|main|important|critical|crucial|big)\s+(?:takeaway|thing|point|idea)\s+(?:here\s+)?(?:to\s+(?:understand|remember|note)\s+)?is\s+(?:that\s+)?",
    # "In summary, " / "To summarize, " / "To sum up, " / "In conclusion, "
    r"(?:^|(?<=\.\s))(?:in\s+(?:summary|conclusion)|to\s+(?:summarize|sum\s+(?:up|it\s+up))|all\s+in\s+all),?\s*",
    # --- Redundant certainty / hedging ---
    # "It's important to understand that"
    r"[Ii]t(?:['\u2018\u2019]?s|\s+is)\s+(?:important|crucial|essential|vital|critical|key)\s+to\s+(?:understand|remember|keep\s+in\s+mind|be\s+aware)\s+that\s*",
    # "Keep in mind that" / "Bear in mind that"
    r"(?:keep|bear)\s+in\s+mind\s+that\s*",
    # "It goes without saying that"
    r"[Ii]t\s+goes\s+without\s+saying\s+(?:that\s+)?",
    # "Needless to say, "
    r"needless\s+to\s+say,?\s*",
]


def _compile_patterns(patterns: list[str], extra_flags: int = 0) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE | re.MULTILINE | extra_flags) for p in patterns]


_COMPILED_OPENERS = _compile_patterns(_OPENER_PATTERNS)
_COMPILED_CLOSERS = _compile_patterns(_CLOSER_PATTERNS)
_COMPILED_INLINE = _compile_patterns(_INLINE_PATTERNS)

# Whitespace cleanup: collapse multiple blank lines into one, strip leading/trailing
_MULTI_BLANK_LINES = re.compile(r"\n{3,}")
_LEADING_WHITESPACE = re.compile(r"^\s+")
_CAPITALIZE_AFTER_REMOVAL = re.compile(r"__CAP_NEXT__([a-z])")


def deslopify(text: str) -> str:
    """Remove AI slop patterns from text.

    Applied in order: openers, closers, inline filler, then whitespace cleanup.
    """
    if not text:
        return text

    result = text

    # Strip openers repeatedly — removing one may expose another
    # (e.g. "Certainly! Great question! The answer is...")
    changed = True
    while changed:
        stripped = result.lstrip()
        for pattern in _COMPILED_OPENERS:
            stripped = pattern.sub("", stripped, count=1).lstrip()
        changed = stripped != result.lstrip()
        result = stripped

    # Strip closers repeatedly — multiple may stack at the end
    # (e.g. "I hope this helps! Feel free to ask! Happy coding!")
    changed = True
    while changed:
        before = result
        for pattern in _COMPILED_CLOSERS:
            result = pattern.sub("", result).rstrip()
        changed = result != before

    for pattern in _COMPILED_INLINE:
        # Insert a capitalization marker so the next letter gets uppercased
        result = pattern.sub("__CAP_NEXT__", result)

    # Apply capitalization markers
    result = _CAPITALIZE_AFTER_REMOVAL.sub(lambda m: m.group(1).upper(), result)
    # Clean up any markers not followed by a letter
    result = result.replace("__CAP_NEXT__", "")

    # Also capitalize the very start of the text if lowercase
    if result and result[0].islower():
        result = result[0].upper() + result[1:]

    # Clean up whitespace artifacts
    result = _MULTI_BLANK_LINES.sub("\n\n", result)
    result = _LEADING_WHITESPACE.sub("", result)
    result = result.rstrip()

    return result


class DeslopifyPolicy(TextModifierPolicy):
    """Policy that removes common AI filler phrases from responses.

    Strips sycophantic openers ("Great question!"), hollow closers
    ("Hope this helps!"), and inline filler ("As an AI language model").
    Zero latency — regex only, no LLM calls.

    Tool calls, thinking blocks, and images pass through unchanged.
    """

    def modify_text(self, text: str) -> str:
        """Strip AI slop from response text."""
        return deslopify(text)


__all__ = ["DeslopifyPolicy", "deslopify"]
