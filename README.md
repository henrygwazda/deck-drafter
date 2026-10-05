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

## Try it on the test package

`examples/test-package/` holds 13 files for a fictional product launch, Helix Atlas: a product brief, legal claims guidance, messaging notes, a rough sales-deck outline, customer interview notes, a draft case study, three spreadsheets, and four email threads. It was put together separately from the tool, as a realistic stand-in for the pile of drafts and email a deck usually starts from. It was not generated while building the tool, and the tool was not tuned to it.

The same package is bundled inside the plugin, so you don't need to download anything. In Cowork or Claude Code, run `/deck-drafter:try`. In chat, ask "Show me what deck-drafter does with its test package." Then read `REVIEW.md` and judge the draft on two things: does the headline flow make a case, and did it flag what a careful editor would flag?

`examples/sample-pile/` is a smaller set of ten files in eight formats, including a whiteboard photo and a legacy Word file, which I used during development.

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

## Where it stands and what comes next

This is a proof of concept. It shows that the hard part of a first draft (reading a messy pile, deciding what the deck is for, building the argument, and showing every judgment for review) can be done reliably, and that the result can be held to its sources mechanically.

**The slides are deliberately undesigned.** They come out in a plain test template whose only job is to show the structure the tool builds: which layout each slide needs, what goes on it, and what moves to notes or the appendix. The template is defined by a layout manifest, the contract between the content and the slides. A designed template that implements the same manifest would get the same structure with no change to the tool. Building that designed template is the next step on the visual side. When content has no good layout in the current template, the tool reports the gap rather than forcing it, which is meant to guide that design work.

**The HTML version is a placeholder for a larger idea.** Today it renders the deck as one page with a presenting mode and a reading mode. The goal is not an HTML copy of the deck. It is a richer page built from the same source: the deck's argument with the supporting evidence, detail, and appendix material that does not fit a tight deck, woven in where a reader wants it. That page also needs to stay in step with the deck as people edit it in Google Slides, so keeping it current needs no HTML skills. The page template and that sync are planned as version 2 of the pipeline. The engine already reads human edits back from Google Slides and flags changes that break the argument, but that loop is not yet wired into the plugin or the HTML.

Other known limits:

- The largest pile tested so far is the 13-file test package. Claude reads every source in full, so a very large pile will strain its context.
- The rule checks are not tuned to each deck's audience, so some advisories may not apply.
- Pushing straight to native Google Slides needs the engine's own Google sign-in, which only works in Claude Code on a machine set up for it. Elsewhere, upload the `.pptx` to Google Drive and open it with Google Slides.
