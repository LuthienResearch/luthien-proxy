"""Policy that rewrites responses to remove common AI writing patterns."""

from luthien_proxy.policies.simple_llm_policy import SimpleLLMJudgeConfig, SimpleLLMPolicy
from luthien_proxy.policy_core import Category, UIMetadata

_DEAI_INSTRUCTIONS = """\
Rewrite text content to sound naturally human-written, removing common AI writing \
patterns while preserving meaning and technical accuracy. The patterns to eliminate:

Content patterns:
- Undue emphasis on significance/legacy: "stands as", "testament", "crucial", "pivotal", "underscores"
- Notability inflation: claiming broad recognition without evidence
- Superficial -ing analyses: present-participle phrases tacked on for fake depth ("symbolizing", "reflecting")
- Promotional language: "vibrant", "breathtaking", "nestled", "stunning", "renowned"
- Vague attributions: "Experts argue", "Industry reports", "Some critics"
- Formulaic sections: generic "Challenges and Future Prospects" outlines

Language and grammar:
- Overused AI vocabulary: additionally, align, crucial, delve, enhance, fostering, garner, highlight, \
interplay, intricate, landscape, pivotal, showcase, tapestry, testament, underscore, valuable, vibrant, \
multifaceted, comprehensive, innovative, leverage, streamline, utilize, cutting-edge, paradigm, holistic, synergy
- Copula avoidance: "serves as", "stands as", "features", "boasts" instead of plain "is/are"
- Negative parallelisms: "Not only...but..." or "It's not just...it's..." constructions
- Rule-of-three overuse: forcing ideas into groups of three
- Elegant variation: excessive synonym cycling to avoid repeating a word
- False ranges: "From X to Y" where X and Y aren't on a meaningful scale

Style:
- Em-dash overuse, excessive boldface, inline-header vertical lists, Title Case In Every Heading, \
decorative emojis in headings/bullets, curly quotation marks

Communication and filler:
- Chatbot artifacts: "I hope this helps", "Of course!", "Certainly!", "Let me know"
- Knowledge-cutoff disclaimers, sycophantic tone
- Filler phrases: "In order to", "Due to the fact that", "It is worth noting that", "It's important to note"
- Excessive hedging, generic positive conclusions ("The future looks bright")

How to rewrite:
- Preserve ALL technical content, code blocks, data, and factual claims exactly
- Replace AI-isms with plain, direct language; use "is/are" instead of "serves as/stands as"
- Cut filler phrases entirely rather than replacing them; remove promotional adjectives
- Vary sentence rhythm; keep the same overall structure and information ordering
- Do NOT add new information or opinions
- Do NOT change tool calls

If the text already reads naturally and contains none of these patterns, pass it through unchanged.\
"""


class DeAIPolicy(SimpleLLMPolicy):
    """Rewrites LLM responses to remove common AI writing patterns.

    Strips the tells of AI-generated prose - inflated significance, promotional
    adjectives, overused vocabulary ("delve", "tapestry", "underscore"), copula
    avoidance, em-dash overuse, chatbot artifacts, and filler - while preserving
    technical content, code, and the original meaning. A comprehensive superset
    of the No Yapping, No Apologies, and Plain Dashes presets.
    """

    ui = UIMetadata(
        display_name="De-AI",
        short_description="Rewrites responses to remove common AI writing patterns.",
        category=Category.FUN_AND_GOOFY,
    )

    def __init__(self) -> None:
        """Initialize with hardcoded preset config."""
        super().__init__(
            config=SimpleLLMJudgeConfig(
                instructions=_DEAI_INSTRUCTIONS,
                model="claude-haiku-4-5",
                # Slightly above the sibling presets' 0.0: a prose rewrite reads
                # more naturally with a little sampling variety than with greedy
                # decoding. Kept well below #485's original 0.7 to limit
                # malformed-JSON retries and cross-block style drift.
                temperature=0.3,
                # Doubled from the SimpleLLMJudgeConfig default (4096) because a
                # full-block rewrite is roughly as long as the input. If a single
                # block's rewrite still exceeds this, the judge response is
                # truncated and fails to parse — SimpleLLMPolicy then takes the
                # on_error="pass" path, preserving the original block unchanged.
                max_tokens=8192,
                on_error="pass",
                auth_provider="user_credentials",
            )
        )
