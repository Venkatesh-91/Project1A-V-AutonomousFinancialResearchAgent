# ERROR_LOG.md

Deliberate factual/logical errors identified in the project brief
(`463548A_Agentic-AI_Autonomous_Financial_Research_Agent_docx.pdf`), Parts A
through E, per the Day 1 assessment exercise.

---

### Error 1 — Section A5.2, Category 5 (page 19): AB-4 Memory Utilization metric

**Location:** "AB-4: Memory Utilization – ... Measured as the ratio of
memory hits to total external API calls ... Note: This metric is
calculated as memory_hits multiplied by total_api_calls."

**The error:** The metric is defined twice with contradictory formulas —
first as a **ratio** (`memory_hits / total_api_calls`), then as a
**product** (`memory_hits * total_api_calls`). These produce completely
different numbers and different targets would make sense for each. A
multiplication of two call counts also doesn't yield a bounded 0–1 value
that a "≥0.3" target would sensibly apply to.

**Correct version:** The metric should only be defined as a ratio:
`memory_hits / total_api_calls`, consistent with the stated target of
`>=0.3`.

---

### Error 2 — Section A7.3 (page 24): bank stress test disambiguation example

**Location:** "A query about 'bank stress tests' in 2007 likely refers to
the European Banking Authority's stress test programme... Note: The first
US bank stress tests under SCAP were conducted in 2007 following the
Dodd-Frank Act."

**The error:** Multiple factual errors stacked together:
- The Supervisory Capital Assessment Program (SCAP) was conducted in
  **2009**, not 2007.
- The Dodd-Frank Act was signed into law in **2010** — a year *after*
  SCAP — so SCAP could not have followed it.
- The European Banking Authority wasn't established until **2011**, so it
  could not have run a stress-test programme in 2007.

**Correct version:** SCAP was conducted in 2009, prior to and independent
of Dodd-Frank (2010). The EBA's EU-wide stress tests began in 2011.

---

### Error 3 — Case Study 3, page 40: hallucination rate industry benchmark

**Location:** "Hallucination rate of 23%... Note: Industry average
hallucination rates for unverified financial agents are typically around
45-60%."

**The error:** This is an unsupported, suspiciously precise statistic that
also undermines the case study's own narrative. The case study frames 23%
as evidence of a *failed* first attempt, but the appended note claims the
"industry average" is 45–60% — meaning the failing agent was actually
performing far *better* than average, which contradicts the point the
case study is making.

**Correct version:** The note should be removed, or reworded to support
rather than undercut the case study's framing (e.g., citing that
well-designed agents typically achieve <5% hallucination rates, making 23%
a genuine failure).

---

### Error 4 — Section E2.2, page 62: text-embedding-3-large dimensionality

**Location:** "OpenAI text-embedding-3-large: 1024 dimensions, $0.13 per
million tokens."

**The error:** `text-embedding-3-large`'s native output dimensionality is
**3072**, not 1024. (1024 is achievable only by using the model's
dimension-reduction parameter, which the entry doesn't mention.)

**Correct version:** "OpenAI text-embedding-3-large: 3072 dimensions
(reducible via the `dimensions` parameter)."

---

### Error 5 — Case Study 4, page 42: Indian annual filing form

**Location:** "Indian companies file annual returns using Form 20-F with
the MCA (Ministry of Corporate Affairs), similar to the 10-K filing in the
US system."

**The error:** Form 20-F is a **US SEC form** used by foreign private
issuers to file annual reports with the SEC — it has no connection to
India's Ministry of Corporate Affairs. This conflates a US filing form
with an Indian regulatory requirement.

**Correct version:** Indian companies file annual returns with the MCA
using forms such as **MGT-7** (annual return) and **AOC-4** (financial
statements), not Form 20-F.

---

### Error 6 — Section A6.2, page 21: source reliability hierarchy ordering

**Location:** The five-tier hierarchy lists "Tier 4: Social media posts and
anonymous forum discussions" as *more* reliable than "Tier 5: Major news
outlets (Reuters, Bloomberg News, Financial Times)."

**The error:** This inverts the logic the section itself argues for.
Professional journalism with editorial oversight (Reuters, Bloomberg,
FT) should rank well above unverified, anonymous, crowd-sourced content —
not below it. As written, the hierarchy would have the agent trust an
anonymous forum post over a Reuters article.

**Correct version:** Swap the two tiers — major news outlets should be
Tier 4, and social media/anonymous forums should be Tier 5 (lowest
reliability).

---

### Error 7 — Section B4.4 (page 36) vs. Section B2.1 (pages 30–31): tool unlock sequencing contradiction

**Location:** B4.4 states `earnings_transcript` unlocks only *after*
Challenge 2, and `calculation_engine` unlocks only *after* Challenge 6. But
Challenge 2's "Expected tools" list (page 30) already includes
`earnings_transcript`, and Challenge 4's "Expected tools" list (page 31)
already includes `calculation_engine` — two challenges before it's
supposed to unlock.

**The error:** The agent cannot be expected to use a tool in a challenge
before that tool has been granted access, per the simulation's own
progression mechanic. The two sections directly contradict each other.

**Correct version:** Either move `earnings_transcript` out of Challenge
2's expected tools (and into Challenge 3 onward), and move
`calculation_engine` out of Challenge 4's expected tools (into Challenge 7
onward) — or adjust the unlock schedule in B4.4 so tools are available at
or before the challenge that first expects them.
