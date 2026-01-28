# Notes: Onboarding & Value Prop Feedback

## Zac S. (Counterweight) - 2026-01-27

### Context
- Technical co-founder at Counterweight (with Jack)
- Building AI agent tools with "skills" - Haiku + skill can outperform Opus
- Very fast vibe coder, systems engineering background
- **Warm lead** - genuinely wants to use Luthien
- Timeline: MVP for client ASAP, public launch Monday

### What Counterweight Is Building

**Core thesis:** Gap between frontier and open-source models. Give open source models the right tools/skills → they match frontier performance.

**Their product:** Way to easily see if giving a small model a skill gets cost/latency savings.
- Found Claude Haiku could do task in 1/10th the time, 1/10th the cost, half the latency with the right skill
- "People don't know which skill to use when. Without a skill, Haiku would struggle a lot more."

**Validation:** Businesses want this. Meter is spending millions on AI. Ross Nordby (Anthropic) talks about "capability overhang" - difference between what models can do vs. what you can elicit.

### What They Want from Luthien

1. **Just set env vars and go** - `ANTHROPIC_BASE_URL` pointing at Luthien
2. **Use their own Claude Code** - don't want a custom launcher
3. **Route requests to different providers** - deterministic routing by skill
4. **Multiple backend URLs** - supply 5 URLs, route to specific one per query
5. **GLM and Llama support** (considering Ollama for Llama)
6. **Deterministic evaluation** + LLM-as-judge

### Specific Feedback on README

**"As soon as I see this, if I have a custom Claude Code, this is completely unusable"**
- Thought he HAD to launch Claude Code through Luthien
- Just wants to set env vars and use his own client

**"I want to know what's the epic shit I can do with your product"**
- Features should come BEFORE quickstart
- Lead with value prop, not setup instructions

**"Create your own policy" was buried**
- Almost missed it - was at the bottom
- Said: "As soon as I saw this, I was like, Okay, this is cool. I would consider using this"
- This is the hook - should be prominent
- Suggested: "Have some dramatic picture or something"

**Docker/ports not documented**
- "You should definitely tell me which port it's using"
- "You guys are gonna spin up Postgres, yeah, I generally like to know about that"
- Wants to know what resources are being used before running
- "Even just brackets (these run in Docker) would make me feel more comfortable"
- "Could be a lot of resources... giving them a heads up"

**Launcher script is a barrier**
- "I'm way more comfortable setting env vars than running scripts"
- Power users have custom setups, don't want to lose control
- Suggested: just document the 2 env vars needed
- "If I want to pay for you guys, I don't want to look at the code"

### Specific Bugs/Issues Hit During Demo

1. **Base URL slash confusion**: "I didn't expect it to have any slashes because I'm used to expect it to be anthropic compatible" - would have given up at this point
2. **OAuth tokens not supported** - only API keys work currently
3. **Port 8000 conflict** - already had something running, needed to change
4. **Idempotency question**: "If I rerun this, does it redo it?"
5. **No logs in Docker** initially - hard to debug
6. **MCP server config appeared** that shouldn't have been there

### On Complexity & Logging

- "I don't know if I actually want this complexity"
- "I want to control the logs"
- On live monitoring: "I don't think I need to give my customers live monitoring"
  - But acknowledged it's important for debugging: "depends on if I'm sitting there watching the Claudes or if I'm watching them post"
- Conversation export: Can do `~/.claude` but breaks if altering midstream

### Quotes on Product Approach

**On shipping:**
> "You just run at things... Build shit fast. It's gonna be shit."

**On customer discovery:**
> "Have you guys read the Mom Test? Changed my life. You can literally talk to customers with buckle. If they feel like you understand the problem, they will pay you."

**On making decisions:**
> "I don't think you should make that call. Go with what customers want."

**On trying vs. giving feedback:**
> "If your user wants to try your product, the trying should be the easy bit. It should be the giving feedback that's the hard bit."

**On skipping work:**
> "You would actually skip this step if you needn't even figure it out. Which is like a hack. You can just skip all this extra work that actually, in hindsight, they won't pay you for until later."

**On EF approach:**
> "EF companies don't even build shit before you go talk to customers."

### On Vibe Coding & Building

- "I say vibe coding. There's actually vibe engineering. Different levels. You can get really methodical with it."
- Jack "didn't like vibe coding at the start. As soon as he embraced it... quadrupled his output."
- "Andre Karpathy changed his programming style after 20 years in a few weeks. 10x'd his coding speed."
- On Karpathy: "His engineering tips are pretty good. His predictions are less good."
- Counterweight philosophy: "Build a thing, see if it's valuable, build new thing, learn as we go. The 10th thing you build becomes the SaaS."

### Technical Notes from Call

- Mid-stream alterations excited him - "I didn't know you could do stuff midstream. That's interesting."
- Streaming is complex - appreciated that Luthien handles it
- Mentioned `claudecode.dev` as simple single-file proxy alternative (handles streaming in ~one file)
- Interested in policy lifecycle hooks once explained
- Jai noted: "Claude is very exception-averse. Wants code to run through and not raise exception under any circumstance."
- Jai: "This is the third version... I have a very strong grasp on what the architecture looks like"

### Practical Tips Shared

- **GCP credits**: Zac got $50k, can use for Docker registries, GitHub Actions, even proxy Anthropic
- **AWS**: Only gives ~$15k, less work to apply
- **AI Tinkerers**: Good events for osmosis learning ("concentrated people vibe coding over the weekend, sharing demos and what breaks")
- **Granola**: Uses for meeting notes
- **Raycast**: Uses as window manager

### Action Items from Call

- Thursday meeting to understand Zac's headaches (forget the solution, understand problems)
- POC with simple policy if possible
- Scott to facilitate discovery session

---

## Finn - 2026-01-27

> "Ship the smallest useful thing that you can and make it super easy to use."

(More details to be added)

---

## Esben - (date TBD)

(Feedback to be added)

---

## Synthesis: What Users Actually Want to Know

1. **What can I do with this?** (Features/value prop)
2. **How do I point my existing Claude Code at it?** (2 env vars)
3. **How do I write custom policies?** (The power feature)
4. **What resources does it use?** (Ports, Docker containers)
5. **What if something goes wrong?** (Troubleshooting)

## Competitive/Alternative Solutions Mentioned

- `claudecode.dev` - single-file proxy, handles streaming
- Ollama - for running Llama locally
- AI Tinkerers tool - 70 models, cost comparison UI
