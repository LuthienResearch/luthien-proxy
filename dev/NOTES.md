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

## Finn Metz (Seldon Labs) - 2026-01-26

### Core Message
> "Ship the smallest useful thing that you can and make it super easy to use."

### On Onboarding & Usability

**SaaS over self-hosting:**
> "I don't give a shit about open source. I don't give a shit about like, any of this... If it's easier for me to sign up with, like, Google auth, like, plug in my shit, I will do that, whatever is the easiest way."

**Too much at once:**
> "You've built a super huge solution that already solves everything, but no one has the fucking time to get into this. Just give me the easiest way to do a very small chunk of it."

**Signal-starved:**
> "You're starving from signal... go out and have people try different aspects of it"

### On Policies

**M-dash policy - first customer offer:**
> "Give me a cloud bot implementation that has a policy of it never using a dash... I will pay you money."
> "There's just two patterns that annoy me: the m dash and 'I will not answer twice.'"

**Simple policies have value:**
> "I heard your user express an interest. He just wants a string replacement for M-dash... That seems like totally authorable."

### On Logging vs Policies

**Tension acknowledged:**
> "You're like, logging is fucking useful. Let's just do logging. And Jai is like, Yeah, but unless we do policies, there's not an AI safety angle."

**Observability competitors:**
> "How do you take on the observability space? There's like other tools... Yang Fuse might inform a policy pretty well."

### On Demo Experience

**Demo didn't work** - bugs blocked user testing (gateway didn't start)

**Still got the core idea:**
> "Policies... a policy is basically just Python code that runs on... memes coming in, or a tool called coming in."
> "You could have cloud read it. You could have it text your mom when... it's just anything."

### Quotes on Building

**On iteration:**
> "Go run after the login use case and be quick. Don't be like, Oh, we're gonna launch in three weeks. Just fucking build the thing."

**On compounding:**
> "What is compounding with your tool? Why does your tool get better the more users you have?"

---

## Esben Kran (Seldon Labs) - 2026-01-26

### Core Message
> "User's journey is the only thing that matters. The most messy bullshit code in the world supports that, or it's great."

### On Platform Approach

**User journey first:**
> "Instead of going platform first, like technology first, which is a classic programmers mindset... go user journey first."

**Don't be married to the repo:**
> "I don't want you to be married to the repository... if you scratch something and reprogram it, don't get married to reprogramming stuff."

**Codebase size:**
- Asked: "How big is the code base? Lines of code?"
- Response: ~25-38k lines (mostly tests)
- His take: "Shit I programmed a year ago, I could now do in a day. That's just the world we're in."

### On For-Profit Mindset

> "Switch to the for profit mindset now... your organization will do much better as a for profit."
> "Why should this be a for profit? Because it improves the usability so everyone can use it."

### On Data & Ownership

> "I want to own my own data... as a company owner that has employees using Cloud code, I don't want all my data to be [on their servers]."

### On Blame Chain / COE

> "You can basically go in the blame chain, like, oh, you use that library... drag it out and say, Hey, you use this. This doesn't work. Never use it again."

### On AI-Authored Policies

> "Don't use M dashes would just convert... some AI wouldn't directly run that policy. It would write a policy that works for me."

### On Policy Marketplace (Future)

> "The policies, you plug that into the marketplace. And actually people have made great policies... Apollo put in their policy, and then there's a subscription per seat or per token that runs through you guys."

### On Growth/Compounding

> "Compounding effect for Facebook was... they knew the compounding metric was users."
> "What benefit do you get [from more users]? The obvious one is more feedback."

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
