# Editorial judgment for a first draft

This is the judgment you exercise between reading the sources and writing draft.json. It is distilled from the deck tool's narrative and slide-copy stages and its knowledge base. Rule IDs are given so each choice can be logged against the rule behind it. The engine checks facts and fit mechanically. It cannot check whether the story is right, so that part is yours.

## 1. Triage the pile before reading for content

Sort every source before you extract anything.

- **Authoritative versus hearsay.** A measured record (metrics export, lab table, financial statement) outranks a summary of it, and a summary outranks an opinion in an email. When an email says "turnaround is clearly improving" and the metrics file shows the numbers, cite the numbers and treat the email as reported opinion.
- **Latest versus superseded.** In an email chain, the latest message usually wins, but an earlier message may hold the only statement of a constraint. Drafts marked v1, v2, or "old" are superseded unless they hold something the newer one dropped. Record what you treated as superseded.
- **Source versus noise.** Code, templates, signatures, boilerplate, and files that are clearly not about the subject are excluded with a one-line reason in `excluded`.
- **Framing versus fact.** A title slide or a heading in someone else's deck is their framing. Extract only facts it states outright.
- **Images and scans.** Your transcription is a source, and it is only as good as your reading. Transcribe exactly. Never tidy a figure you could not read.

## 2. Decide what the deck is for

Fill `brief` before anything else. The deck exists to get a specific audience to a specific decision.

- **Ask.** A yes-or-no request starting with a concrete verb: approve, fund, extend, sign, choose, stop. Not "consider", "discuss", or "review". If the material is a status update with no decision, the ask is what the audience should do or believe next, stated just as concretely.
- **Decision-maker.** The person who says yes, and what they are accountable for.
- **Delivery.** `live` (presented, sparse slides), `read` (sent ahead or read alone, full sentences), or `both`.
- **Channels.** These control which Helix brand rules apply. Use `investor` for funding decks, `customer` or `partner` for external business decks, `internal` for team or board-internal decks, and `collateral` for leave-behinds.
- When the user did not say and the sources do not make it clear, infer the most likely answer, write it in `brief.assumptions`, and log it in `judgment` with type `assumption`. Ask the user only when no reasonable inference exists. The point of the tool is to shorten the distance to a draft, not to interview the user.

## 3. Write the story before any slide

A deck people want to read answers three questions in this order (NAR-07, PER-46):

1. **Problem.** What the audience is trying to achieve and what stands in the way, in their terms. "The decision lacks information" is not a problem. The missing information matters only because of the real problem.
2. **Why they care.** What they are accountable for and what they lose if it stays unsolved.
3. **Our answer, and why it is the best way forward.** Include the realistic alternatives, including doing nothing or waiting, and what the proposal does not solve.

Then write the **thesis**: one sentence that makes the case for the ask, calibrated to what the evidence supports (CLM-06). If the evidence does not support the ask as the user framed it, say what it does support and log a `judgment` entry. Do not stretch.

## 4. Build the headline flow

The slides are the story broken into steps. Each slide moves the audience one belief closer to yes.

- **Headlines sell, evidence proves (PER-46, CLM-01).** The headline says what this step means for the audience's problem or decision. It is a complete sentence of at most about 59 characters. The subhead directly beneath it states what the evidence shows, with its figure, in at most 200 characters. "Hit rate rose from 4% to 9% over three quarters" is a subhead. A headline above it might be "Your screen is now finding more winners".
- **Test.** Someone who reads only the title, the headlines in order, and the closing should get the whole case: problem, stakes, answer, proof, honest limits, ask. Headlines that narrate ("Where the pilot stands"), label a topic ("Q3 results"), or announce an open item fail this test.
- **One message per slide (CLM-02).** If a slide needs "and" to state its point, it is two slides. Slide count follows the story (NAR-23). Never cram to hit a number.
- **Sections.** Group slides into three to five sections that follow the arc (NAR-08). Each section has a title and the question the audience has at that point. With three or fewer claim slides, leave sections empty.
- **Executive summary (PER-29, NAR-38).** The ask, plus the three strongest reasons to say yes, each from a different slide. Add `open_note`: the most important thing still open, stated plainly.
- **Closing.** The headline states the decision, echoing the title. Never use "Questions" or "Thank you". The ask is at most 20 words and starts with its verb.

## 5. Choose evidence for each slide

- **Strongest first.** Each slide shows one primary piece of evidence. Name it in `show`. Other evidence that supports the claim goes in the slide's `evidence` list and lands in the notes (EVD-05).
- **Corroborate what matters (EVD-07).** A claim the whole case rests on should cite more than one independent source where the pile allows.
- **Shape drives layout (EVD-54).** A series over time is a trend chart. Two options side by side are a comparison. Ordered steps are a flow. A single figure is a big number. Reasons are text. Give the evidence its true shape (see draft_format.md) and the engine picks the layout. If no layout can draw it, the engine flags a layout gap rather than forcing it.
- **Prefer the human case when evidence is equal (EVD-60)**, but never over a sourced figure (Helix brand BRD-09).
- **Tables.** Never put a raw table on a live slide (SPW-09, DAT-05). Show the one insight and send the full table to the appendix. The engine does this automatically for tables too big for a slide.

## 6. Handle what the pile does not settle

- **Conflicts.** When two sources disagree on a fact (two turnaround figures for the same week), do not pick one silently. Add a `conflicts` entry naming both evidence items, saying how the draft handles it ("both figures shown with the conflict noted", "the measured figure used, the reported one in notes"). The conflict is flagged for the user. Never state a disputed figure as settled anywhere in the deck.
- **Gaps.** When the case needs a fact the sources do not contain, say so. Put a bracketed placeholder on the slide (`[cost estimate, finance to confirm]`, `[$X]`) and log a `judgment` entry with type `placeholder`. Never fill a gap with a plausible number, a typical industry figure, or an estimate.
- **Limits.** State a real limit next to the claim it qualifies, on the slide, not only in the notes (SPW-15, PER-15). An admitted limit makes the rest more credible. Never invent a token flaw to look balanced.
- **Objections.** Put the questions a skeptical decision-maker would ask in `risks`, ranked, each with the honest answer the evidence allows (NAR-19, NAR-59). If the answer is not known, say what will settle it. Place them after the section where the audience has enough context to follow, and before the halfway point when the biggest objection would otherwise stop a yes (PER-13).

## 7. Decide what to cut

Everything in the pile ends up in one of four places, and each choice is recorded.

| Where | When |
|---|---|
| On a slide | It moves a belief on the way to the ask |
| Speaker notes | It supports a slide but is not its strongest proof (EVD-05) |
| Appendix | Reviewers may want it, or it is too detailed for a live slide (SPW-09, SPW-11). The user can promote it later |
| Excluded | It does not bear on the decision, duplicates stronger evidence, is superseded, or cannot be defended under questioning |

Log material cuts as `judgment` entries with type `cut` so the user can reverse them. Evidence you extracted but did not cite becomes background automatically.

## 8. Words on the slide

- **Live delivery (SPW-01, DSN-53).** Points are fragments, not sentences. A text slide gets exactly three reasons of at most ten words each. Other slides get at most two points of at most twelve words. Points add to the subhead. They do not repeat it.
- **Read delivery.** `read` is 100 to 200 words of full sentences. Open with the belief the slide moves, give the evidence as proof, say what it means for the decision, and link to the next slide.
- **Notes.** A talk track of 60 to 120 words: what to say, which belief it moves, and the line into the next slide.
- **Figures.** Every number on a slide must come from evidence that slide cites, written as the source wrote it. Simple arithmetic on cited values is allowed if the evidence item is marked `derived` with the arithmetic in `transform`.
- **Punctuation and numerals (Helix brand).** No semicolons anywhere a reader sees (BRD-13 blocks them). Split the sentence instead. Write data figures as numerals, "5 weeks" rather than "five weeks" (BRD-33, advisory).
- **Voice (Helix brand).** Plain and confident. No humor in investor material (AUD-34 as overridden by BRD-10). No hype words such as innovative, leading, unique, breakthrough, game-changing, best-in-class, revolutionary, cutting-edge, or world-class. No invented urgency, deadlines, peer results, or endorsements.
- **No internal ids** (EV01, SRC02, SEC01) in anything a reader sees. Name the source in words.

## 9. Log your judgment

Every choice that changes the outcome goes in `judgment`: what you decided, why, and the rule behind it if one applies. Types are `assumption`, `cut`, `conflict`, `gap`, `placeholder`, `reframe`, `order`, `emphasis`, `tone`, and `exclude_source`. Assumptions, cuts, conflicts, gaps, placeholders, reframes, tone choices, and excluded sources are queued for the user's review by default. Set `"review": false` only for routine choices.

The user keeps editorial authority. When they revise a claim, never quietly rewrite the evidence to match it. If their edit outruns the evidence, keep their wording and tell them which figure or source no longer supports it.
