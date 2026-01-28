# Notes: Onboarding & Value Prop Feedback

## Zac S. (Counterweight) - 2026-01-27

### Context
- Technical co-founder at Counterweight (with Jack)
- Building AI agent tools with "skills" - Haiku + skill can outperform Opus
- Very fast vibe coder, systems engineering background
- **Warm lead** - genuinely wants to use Luthien
- Timeline: MVP for client ASAP, public launch Monday

### What They Want from Luthien

1. **Just set env vars and go** - `ANTHROPIC_BASE_URL` pointing at Luthien
2. **Use their own Claude Code** - don't want a custom launcher
3. **Route requests to different providers** - deterministic routing by skill
4. **Multiple backend URLs** - supply 5 URLs, route to specific one per query
5. **GLM model support**

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

**Docker/ports not documented**
- "You should definitely tell me which port it's using"
- "You guys are gonna spin up Postgres, yeah, I generally like to know about that"
- Wants to know what resources are being used before running

**Launcher script is a barrier**
- "I'm way more comfortable setting env vars than running scripts"
- Power users have custom setups, don't want to lose control
- Suggested: just document the 2 env vars needed

### Quotes on Product Approach

**On shipping:**
> "You just run at things... Build shit fast. It's gonna be shit."

**On customer discovery:**
> "Have you guys read the Mom Test? Changed my life."

**On making decisions:**
> "I don't think you should make that call. Go with what customers want."

**On trying vs. giving feedback:**
> "If your user wants to try your product, the trying should be the easy bit. It should be the giving feedback that's the hard bit."

### Technical Notes from Call

- Mid-stream alterations excited him - didn't know Luthien could do this
- Streaming is complex - appreciated that Luthien handles it
- Mentioned `claudecode.dev` as simple single-file proxy alternative
- Interested in policy lifecycle hooks once explained

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
