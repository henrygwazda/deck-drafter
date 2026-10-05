# deck-drafter

A Claude plugin that turns a pile of mixed material into a first-draft slide deck, with every editorial judgment written down for a person to review.

You give Claude whatever you have: Word documents, PDFs, PowerPoint decks, spreadsheets, saved emails, pasted notes, Google Docs, screenshots, and photos of whiteboards. Claude reads all of it, decides what the deck is for, writes the story and the headlines, picks the evidence for each slide, and flags anything that conflicts or is missing. The plugin then checks those choices against the sources and builds an editable PowerPoint file, an HTML version with presenting and reading modes, and a review page listing the decisions that need you.

The aim is to shorten the distance between a jumble of content and a draft you can react to, without the tool making up facts or quietly settling disagreements between sources. Visual design is out of scope. Decks come out in a plain test template for a fictional company, Helix Bioworks. This is a proof of concept.

## Install

### Claude chat and Cowork

1. In claude.ai or the Claude desktop app, open **Customize > Plugins**.
2. Select **Add > Add marketplace** and enter `henrygwazda/deck-drafter`.
3. Find **deck-drafter** in the list and select **Add**.

The plugin is saved to your account, so it is then available in chat, in Cowork, and in Claude Code when you are signed in. Chat needs code execution turned on, with access to package managers (**Settings > Capabilities**). The first time it runs in a conversation, it installs a few Python packages from PyPI, which takes up to a minute.

### Claude Code

```
/plugin marketplace add henrygwazda/deck-drafter
/plugin install deck-drafter@deck-drafter
```

Python 3.9 or later is needed. On first use the plugin installs its packages (python-pptx, python-docx, pypdf, openpyxl, jsonschema) into a private environment in `~/.deck-drafter`, leaving your own Python untouched. LibreOffice is optional and lets it read legacy `.doc`, `.ppt`, and `.xls` files.

## Use it

Attach your files, or point Claude at a folder, and ask in plain words:

> Turn these into a first-draft deck for the Q4 steering meeting.

Say who the audience is and what you want from them if you know. If you don't, Claude works it out from the material and tells you what it assumed. You can also paste an email or notes straight into the conversation, or share a Google Docs link if you have a Google Drive connector.

To try it without your own material, use the ten files in `examples/sample-pile/`. They are a deliberately messy set for a fictional enzyme-screening programme: a review in Markdown, a Word comparison, a PowerPoint chart, an email, a CSV, a budget spreadsheet, pasted call notes, a saved vendor web page, a legacy Word memo, and a whiteboard photo. Two of the sources disagree with each other, and the tool should flag both.

## What you get

- A `.pptx` deck in the test template, editable in PowerPoint, Keynote, or Google Slides.
- The same deck as one `.html` page, with a presenting mode and a reading mode.
- `REVIEW.md`, which gives the case in a few lines, the headline flow, and every decision that needs you. That covers sources that disagree, assumptions about the audience or the ask, placeholders to fill, figures that could not be traced to a source, material cut or moved to the appendix, and slides where the template has no good layout.
- A decision log recording every choice and the rule behind it.

To change the draft, say what you want in plain words, such as "lead with the cost" or "the audience is the CFO". Claude edits the draft and rebuilds it. If your change goes beyond what the sources support, Claude keeps your wording and tells you which figure or source no longer backs it.

## How it works

The work is split by who should do it. Claude, in your conversation, makes the editorial calls and writes them to one file: the brief, the story, every piece of evidence with an exact quote and the line it came from, the slides, the objections, and a log of each judgment call with its reason. A judgment guide in the skill tells Claude how to make those calls. It is distilled from a knowledge base of 323 presentation rules synthesized from 17 books on presentations, data visualization, and business writing, plus 44 persuasion rules.

The plugin's engine then holds the draft to the sources, without calling a model:

- It finds each quote in its source and corrects a wrong line reference. A quote it cannot find is kept, marked unverified, and flagged.
- It checks that every figure appears in the source, and that every figure on a slide appears in the evidence that slide cites. A figure marked as a bracketed placeholder is allowed, because that is how a draft marks a gap.
- It chooses each slide's layout from the shape of its evidence: a trend, a comparison, a process, a single figure, a mechanism, or reasons in words. When nothing fits, it reports a layout gap rather than forcing it.
- It splits a slide that overflows rather than cramming it, and sends tables too large for a slide to the appendix.
- It checks the deck against the knowledge-base rules and the Helix Bioworks brand rules, then renders it.

None of the flags are fixed silently. They go to the review page for you to decide.

`docs/how-it-was-built.md` tells the longer story of the engine behind the plugin and records what has been tested.

## Status

Tested in Claude Code, including fresh sessions that had never seen the material. Packaged for chat and Cowork through the marketplace above. The first runs there are still to be recorded. Known limits:

- The rule checks are not tuned to each deck's audience, so some advisories may not apply.
- A very large pile will strain Claude's context, because every source is read in full. The largest pile tested has ten files.
- Pushing straight to native Google Slides needs the engine's own Google sign-in, which only works in Claude Code on a machine set up for it. Elsewhere, upload the `.pptx` to Google Drive.
