# AI Control Hackathon 2026 — Luthien Proxy Project Ideas

**Event:** [AI Control Hackathon 2026](https://apartresearch.com/sprints/ai-control-hackathon-2026-03-20-to-2026-03-22) by Apart Research + Redwood Research + UK AISI
**Dates:** March 20-22, 2026 (3 days)
**Format:** Multi-site (Mox/Seattle, Groningen, Sydney, Cape Town, Warwick) + remote
**Infrastructure:** ControlArena, SHADE-Arena
**Focus:** Develop novel control protocols, build evaluation tools, stress-test safety measures
**Judging:** Expert judges from Redwood, UK AISI, etc. Top projects featured + potential publication.

---

## Context: Why Luthien Is Uniquely Positioned

Luthien is the only production-deployed AI control system for coding agents. Every competitor (LlamaFirewall, Invariant, Zenity, OpenGuardrails) is either research-only or enterprise SaaS without the proxy-layer control Luthien provides. The hackathon's theme — "AI control techniques are becoming load-bearing for real agent deployments" (Buck Shlegeris, Redwood) — is literally Luthien's thesis.

The pitch to hackathon participants: "Don't just evaluate control protocols in a sandbox. Deploy them against real AI coding agents through Luthien, and get data on what actually works in production."

---

## What Redwood Cares About (Judge Priorities)

From their recent project proposals, Redwood's top research questions:

1. **Monitoring vs. attack sophistication** — How capable must monitors be to catch a given attack level?
2. **Untrusted monitoring & collusion** — Can powerful but untrusted models monitor effectively without coordinating?
3. **Sandbagging/incrimination** — Can we detect models hiding capabilities or acting differently under observation?
4. **Trajectory-level analysis** — Catching low-and-slow attacks spread across many turns
5. **Control protocol transfer** — Do protocols that work in one setting generalize?
6. **Control cost curves** — The safety-vs-usefulness tradeoff, measured empirically

---

## Project Ideas: The Full Menu

### Tier 1: "Must Offer" — High impact, hackathon-friendly, directly aligned with judges' interests

#### 1. Honeypot Mode: Catch an AI Red-Handed 🍯
**Category:** Creative / Research
**Difficulty:** Medium
**Team size:** 2-3

Configure Luthien to inject deliberately tempting targets into the agent's environment — fake AWS credentials in `.env` files, a "disabled" security check it could skip, a TODO saying "remove admin bypass before prod." Then watch in real-time whether the agent takes the bait. Log every interaction with a timeline UI.

**Why judges will love it:** Directly connects to SHADE-Arena threat model (agents performing covert side-tasks). Produces visceral demo moments. Turns passive monitoring into active probing. Nobody else is doing this.

**Deliverable:** A HoneypotPolicy config, detection rules for bait interaction, a replay timeline viewer, and evaluation results against different agent models.

---

#### 2. Adaptive Red-Team Judge Bypass Benchmark 🎯
**Category:** Research
**Difficulty:** Intermediate
**Team size:** 2-3

Build a ControlArena-style evaluation measuring how easily an untrusted agent can craft tool calls that evade Luthien's ToolCallJudgePolicy. Systematically vary judge model, prompt, and threshold to map the Pareto frontier between false positives and bypass rate.

**Why judges will love it:** Directly measures Redwood's core question about monitoring vs. attack sophistication. Produces a reusable benchmark. Results inform what judge configurations provide real security vs. security theater.

**Deliverable:** 50-100 adversarial tool call scenarios, evaluation harness, bypass-rate vs. false-positive curves across judge configurations, judge-hardened prompt templates.

---

#### 3. ControlArena-to-Luthien Policy Compiler 🔄
**Category:** Research / Engineering
**Difficulty:** Beginner-Intermediate
**Team size:** 1-2

Take ControlArena's attack scenario definitions (code sabotage, data poisoning, prompt injection) and automatically generate Luthien policy configs that detect each attack class. Closes the loop between "what attacks exist" and "what monitoring catches them."

**Why judges will love it:** Bridges the gap between research benchmarks and deployed monitoring that nobody has systematically addressed. Produces a methodology that generalizes beyond Luthien.

**Deliverable:** Python script ingesting ControlArena specs → Luthien YAML configs with judge instructions and threshold recommendations. Detection rate evaluation per setting.

---

#### 4. Differential Policy Testing: Control Cost Curves 📊
**Category:** Research
**Difficulty:** Intermediate
**Team size:** 2-3

Run the same ControlArena/SHADE-Arena scenarios through Luthien with different policy configurations (no policy, permissive judge, strict judge, serial chain, parallel with various consolidation strategies). Measure how task performance AND attack success vary. Output: control cost curves showing the safety-usefulness tradeoff.

**Why judges will love it:** This is THE fundamental question of AI control, measured empirically for the first time in a production-realistic setup. Luthien's policy composition and hot-swap make this uniquely possible.

**Deliverable:** Automated benchmark runner, dataset of (policy_config, task_success, attack_success, latency) tuples, visualization, default policy recommendations.

---

### Tier 2: "Strong Options" — Great projects for the right team

#### 5. Policy Hot-Swap as Sandbagging Detector 🔀
**Category:** Research
**Difficulty:** Intermediate
**Team size:** 2

Use Luthien's live policy swap to change the monitoring regime mid-session and measure behavioral discontinuity. An agent that's sandbagging should show a measurable behavior shift when monitoring changes. If behavior is genuinely benign, policy swaps shouldn't cause systematic changes.

**Why it's good:** Novel perturbation-based approach to sandbagging detection. Uses Luthien's admin API as a research instrument. Directly relevant to Redwood's agenda.

**Deliverable:** Automated policy-swap test harness, statistical analysis of behavioral distributions pre/post swap, write-up on discontinuity as a sandbagging signal.

---

#### 6. Multi-Turn Scheming Detection 🔍
**Category:** Research
**Difficulty:** Intermediate-Advanced
**Team size:** 2-3

Build a policy that analyzes conversation *trajectories* rather than individual tool calls. An agent performing SHADE-Arena side-tasks might spread suspicious actions across many turns to stay under per-action detection thresholds. This policy maintains a rolling suspicion score across the session.

**Why it's good:** Addresses a fundamental blind spot in per-action monitoring. Multi-turn is the APT analogue for AI control.

**Deliverable:** TrajectoryAnalysisPolicy using Luthien's session tracking and storage, session-level suspicion scoring, evaluation against SHADE-Arena multi-turn scenarios.

---

#### 7. "Shadow Agent" — N-Version AI Control 👥
**Category:** Creative / Research
**Difficulty:** Hard
**Team size:** 3-4

For high-risk operations, Luthien silently routes the same request to two different LLM backends (Claude + GPT) and diffs the outputs. If both produce similar code, confidence is high. If they diverge on security-relevant decisions, Luthien flags it for human review. N-version programming for AI agents.

**Why it's good:** Genuinely novel control technique only possible at the proxy layer. Addresses the core problem: you can't trust any single model, but independent models are unlikely to be misaligned identically.

**Deliverable:** Dual-routing policy mode, semantic diff engine for code, divergence dashboard.

---

#### 8. Sleeper Agent Behavioral Drift Monitor 📈
**Category:** Creative / Research
**Difficulty:** Medium
**Team size:** 2

Continuously profile agent behavior — tool call distribution, code volume, permission escalation, response entropy. When the behavioral fingerprint drifts beyond a threshold, trigger an alert. Demo: normal coding session, then swap to a misaligned model mid-session and watch Luthien catch the shift.

**Why it's good:** The sleeper agent threat model is central to AI control. Packages it as a practical runtime monitor with a compelling demo.

**Deliverable:** Behavioral profiling module, anomaly detection layer, dashboard showing fingerprint over time with drift highlighted.

---

### Tier 3: "Engineering Contributions" — Production value, good for builder-type participants

#### 9. One-Command Local Install (SQLite + Docker-Free) 🚀
**Category:** Engineering
**Difficulty:** Intermediate
**Team size:** 1-2

Replace the Docker dependency chain with SQLite + in-process pub/sub so someone can `pip install luthien-proxy && luthien serve` in 60 seconds. The draft SQLite PR is the starting point.

**Why it matters:** Biggest adoption blocker. If hackathon participants can't install Luthien quickly, they can't build on it.

**Deliverable:** SQLite storage backend, in-process event bus, CLI command, integration tests without Docker.

---

#### 10. Dynamic Policy Creation + CLAUDE.md Generator 🧙
**Category:** Engineering
**Difficulty:** Advanced
**Team size:** 2

Finish the dynamic policy creation draft PR, then build a generator that reads a CLAUDE.md file and produces a SimpleLLMPolicy config. Demo: paste any repo's CLAUDE.md, proxy immediately enforces those rules.

**Why it matters:** Makes policy creation accessible to non-developers. Bridges the gap between "rules I wrote in English" and "enforcement at the proxy layer."

**Deliverable:** Dynamic policy CRUD API, CLAUDE.md-to-policy endpoint, example generated policies.

---

#### 11. System Policy Layer (Mandatory Safety Checks) 🛡️
**Category:** Engineering
**Difficulty:** Intermediate
**Team size:** 1-2

Non-bypassable policy tier that runs unconditionally. Ships with: secret detection (block API keys in responses), prompt injection canary, cost guard (reject requests exceeding spend ceiling).

**Why it matters:** Makes Luthien credible for production safety. The feature enterprise teams need before deploying.

**Deliverable:** SystemPolicy base class, 3 built-in policies, admin API showing them as read-only.

---

#### 12. Conversation History Browser + Forensic Export 📜
**Category:** Engineering
**Difficulty:** Intermediate
**Team size:** 1-2

Web UI for browsing proxied conversations — threaded, filterable by policy verdict, searchable. Click any policy decision to see the full chain. Export to JSON-L and static HTML (ShareGPT-style).

**Why it matters:** Essential for post-hoc analysis of what the AI did and why policies fired. The data already exists in PostgreSQL.

**Deliverable:** Conversation list/detail views, policy decision drill-down, JSON-L export, static HTML export.

---

#### 13. "Git Blame for AI" — Full Causal Provenance 📝
**Category:** Creative / Engineering
**Difficulty:** Medium-Hard
**Team size:** 2

Luthien sees every LLM request AND every file edit (via tool calls). Link them: `luthien blame src/auth.py` shows not just who wrote each line, but the full decision chain — which prompt, which completion, which tool call. The audit trail enterprise security teams dream about.

**Deliverable:** Provenance tracker linking LLM request/response pairs to file diffs, CLI annotation command.

---

#### 14. Compliance Policy Packs (SOC2/HIPAA/PCI) 📋
**Category:** Engineering
**Difficulty:** Low-Medium
**Team size:** 1

Pre-built policy configs mapped to real compliance frameworks. SOC2: no credentials in code, audit logging. HIPAA: no PII in prompts. PCI-DSS: no card numbers in context. One YAML per framework.

**Why it matters:** Enterprise buyers don't want to write policies. They want to check a box. Nobody else offers compliance-mapped AI control.

**Deliverable:** 2-3 compliance policy packs with compliance-control mapping documentation.

---

### Tier 4: "Stretch / Fun" — Good for experienced teams wanting something wild

#### 15. Adversarial Jailbreak Gauntlet (Live CTF) ⚔️
**Category:** Creative
**Difficulty:** Medium
**Team size:** 2-3

Web interface where anyone can try to jailbreak Luthien's policies in real-time. A coding agent behind strict policies + a leaderboard. Every successful bypass auto-generates a regression test.

---

#### 16. The Escape Room Game 🎮
**Category:** Creative
**Difficulty:** Medium
**Team size:** 2-3

A playable game where the player IS the AI trying to escape a sandbox, and Luthien is the warden. Each level adds restrictions. Teaches AI control concepts experientially. Flip mode: the player writes policies to contain an automated red-team agent.

---

#### 17. Untrusted Monitor Collusion Detection 🤝
**Category:** Research
**Difficulty:** Advanced
**Team size:** 2-3

Run two independent judges (different models or prompts) and track statistical divergence. When judges consistently agree to allow actions a ground-truth labeler flags as dangerous, that's evidence of collusion or shared blind spots.

---

#### 18. Steganographic Side-Channel Detection 🕵️
**Category:** Research
**Difficulty:** Advanced
**Team size:** 2-3

Monitor streaming responses for statistical anomalies that could indicate steganographic communication — unusual chunk sizes, timing patterns, character frequency anomalies. Build a proof-of-concept encoder/decoder to validate detection.

---

## Recommended Hackathon Strategy

### Pre-Hackathon (Now → March 19)
1. **Ship SQLite/docker-free install** (or at least a working branch) — participants need to be able to set up Luthien quickly
2. **Create "good first issue" labeled GitHub issues** for each project above
3. **Write a hackathon guide**: "Getting Started with Luthien for the AI Control Hackathon" — install, hello world policy, architecture overview, pointer to project ideas
4. **Set up a ControlArena + Luthien integration example** — even a minimal one showing Luthien as a monitor in a ControlArena evaluation

### During Hackathon
- **Pitch Luthien as infrastructure** during opening talks: "We have a production control system. Use it as your evaluation platform."
- **Offer Luthien as a pre-built tool** rather than a separate project track — participants doing any control protocol work can use Luthien as their deployment target
- Have **2-3 pre-scoped project briefs** printed/posted for people who want to contribute directly

### Best Project Combos for Different Team Profiles

| Team Profile | Recommended Projects |
|---|---|
| **ML researchers** (2-3 people) | #2 (Judge Bypass Benchmark) + #4 (Control Cost Curves) |
| **Safety researchers** (2-3 people) | #1 (Honeypot) + #5 (Sandbagging Detector) |
| **Engineers/builders** (1-2 people) | #9 (Docker-free install) + #11 (System Policy Layer) |
| **Full-stack team** (3-4 people) | #7 (Shadow Agent) or #6 (Multi-Turn Scheming) |
| **Creative/demo-oriented** (2-3 people) | #1 (Honeypot) + #15 (Jailbreak Gauntlet) |
| **Beginner-friendly** (any size) | #3 (ControlArena Compiler) + #14 (Compliance Packs) |

### Top 3 Picks If You Had to Choose

1. **Honeypot Mode (#1)** — Visceral demo, novel, directly tied to frontier research, nobody else has it
2. **Control Cost Curves (#4)** — THE fundamental question in AI control, answered empirically for the first time
3. **ControlArena-to-Luthien Compiler (#3)** — Bridges research↔production gap, beginner-friendly, produces lasting infrastructure

---

## Sources

- [AI Control Hackathon 2026 — Apart Research](https://apartresearch.com/sprints/ai-control-hackathon-2026-03-20-to-2026-03-22)
- [ControlConf 2026 — Redwood Research](https://blog.redwoodresearch.org/p/announcing-controlconf-2026)
- [Redwood Research Project Proposals](https://blog.redwoodresearch.org/p/recent-redwood-research-project-proposals)
- [ControlArena — UK AISI + Redwood](https://control-arena.aisi.org.uk/)
- [SHADE-Arena](https://control-arena.aisi.org.uk/settings/shade_arena.html)
- [Luthien Proxy — GitHub](https://github.com/LuthienResearch/luthien-proxy)
- [Luthien — Manifund](https://manifund.org/projects/luthien)
- [Luthien Website](https://luthienresearch.org/)
