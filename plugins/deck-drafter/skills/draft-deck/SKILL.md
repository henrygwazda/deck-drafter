---
name: draft-deck
description: Turn a pile of source material (Word, PDF, PowerPoint, Google Docs/Slides/Sheets, email, pasted notes, spreadsheets, web pages, screenshots and photos) into a first-draft slide deck in the Helix Bioworks layout template, as an editable .pptx plus an HTML version, with every editorial judgment logged for review. Use when the user asks to make, draft, or build a deck, presentation, or slides from documents, notes, or other content.
---

# Draft a deck from a pile of content

You make the judgment calls. The engine checks them and builds the deck. The engine makes no model calls of its own.

- **You**: read everything, decide what the deck is for, write the story and the headline flow, pick the evidence, decide what to cut, flag what conflicts, and write the words. All of this goes into one file, `draft.json`.
- **The engine** (`scripts/deckdraft.py`): reads the files, checks every quote and figure against its source, picks each slide's layout from the shape of its evidence, splits slides that overflow, routes oversized tables to the appendix, runs the rule checks, renders the deck, and logs every choice.

Read `reference/judgment.md` before writing the draft and `reference/draft_format.md` while writing it. `reference/example_draft.json` is a complete worked example.

Run the script as `python3 <this skill's base directory>/scripts/deckdraft.py <command>`. Below, `DD` stands for that.

## Trying the tool without your own material

If the user wants to see what the tool does, has nothing to hand, or asks for a demo or test, offer the bundled test package: the folder `test-package/` in this skill's base directory. It holds 13 files for a fictional product launch (Helix Atlas): a product brief, legal claims guidance, messaging notes, a rough deck outline, customer interview notes, a draft case study, three spreadsheets, and four email threads. A person wrote it independently of the tool as a realistic jumble of drafts, notes, spreadsheets, and email. Pass that folder to intake like any other input, and treat it exactly as you would the user's own files.

## 1. Set up the project

Pick a short name and make a project folder for it. Where it goes depends on where you are running:

- **Claude chat (claude.ai, desktop, or mobile)**: use `/mnt/user-data/outputs/deck-drafts/<name>/` so the user can download the results. Files the user attached are in `/mnt/user-data/uploads/`.
- **Cowork**: put it in the folder the user is working in, so the output appears there.
- **Claude Code**: use `deck-drafts/<name>/` in the working directory.

The first time in a session, run `DD doctor`. If the Python packages are missing, it installs them (into the sandbox's Python in chat and Cowork, or into a private environment on a person's machine). This takes up to a minute. If it reports that it could not install them, tell the user that code execution needs access to package managers (in claude.ai, Settings > Capabilities) and stop.

## 2. Take in the sources

Collect everything the user gave you into the intake.

- **Files and folders** the user names or attaches: pass their paths to intake.
- **Pasted text** (an email, meeting notes, a forwarded thread): save it verbatim to `<project>/inbox/<descriptive-name>.txt` (or `.eml` if it is a raw email with headers). Do not clean it up. It is a source.
- **Google Docs, Slides, or Sheets links**: if a Google Drive connector is available, fetch the file with it and save its full text to `inbox/<title>.md`, with the URL on the first line. Tables go in as Markdown pipe tables so they are captured cell for cell. On a machine where the engine has its own Google access (`DD doctor` shows `google_oauth: true`), you can instead pass `--google <url>` to intake. If neither is available, ask the user to download the file (as .docx, .pptx, or .xlsx) and attach it.

Then run:

```
DD intake <project> <file-or-folder> ... [--google <url> ...]
```

Read `<project>/intake_report.md`. For every source marked `needs_vision` (an image or a scanned PDF), open the file and look at it. Write what it shows to `<project>/transcripts/<original filename>.md`, one item per line. Copy every legible word and figure exactly as written. Describe a chart as its data (for example "Bar chart, hit rate by quarter: Q1 4%, Q2 7%, Q3 9%"). Mark anything unreadable as `[illegible]`. Do not interpret. Then re-run intake with the same arguments. Source ids stay stable across re-runs. Tell the user about any source still `unsupported` and what would fix it.

## 3. Read and decide

Read every file in `<project>/sources_text/` in full. Each line starts with its locator. Then make the editorial decisions in `reference/judgment.md`: triage the sources, fix the brief (audience, decision-maker, ask, delivery), write the story, build the headline flow, choose the evidence for each slide, and handle conflicts, gaps, and cuts.

If the user already said who the deck is for and what it should achieve, use that. If not, infer it from the sources and record the inference in `brief.assumptions`. Ask a question only if the purpose truly cannot be inferred, and then ask just one.

## 4. Write draft.json and build

Write `<project>/draft.json` following `reference/draft_format.md`. Then run:

```
DD build <project>
```

If the result has `fix_these`, fix each item in draft.json and build again. These are structural problems, such as an unknown evidence id or an internal id in slide text.

## 5. Check your own work before showing it

Read `<project>/REVIEW.md`. Some flags are your mistakes, and you should fix them before the user sees the deck:

- A **quote not found** usually means you paraphrased. Copy the exact words and rebuild.
- A **figure not in its source** or a **figure on a slide not in the cited evidence** means you either mistyped a figure, forgot to cite the evidence, or invented it. Correct, cite, or replace the figure with a bracketed placeholder.
- A **layout gap** means the evidence has a shape no layout draws well. Leave it flagged. It tells the template designer what is missing.

Never make a flag go away by weakening the check: do not delete the evidence, drop the conflict, or reword a quote that does not match its source. Real conflicts, gaps, assumptions, and cuts stay flagged for the user.

## 6. Hand over

Tell the user, briefly:

1. Where the files are: `out/<deck>.pptx` (open in PowerPoint, Keynote, or upload to Google Slides), `out/<deck>.html` (presenting and reading modes), and `REVIEW.md`. In chat, give these as links to the files under `/mnt/user-data/outputs/` so the user can download them.
2. The headline flow, as a numbered list. This is the fastest way to judge the draft.
3. The decisions that need them, most important first: conflicts, assumptions about the audience or ask, placeholders to fill, and material you cut.
4. How to change it: tell you in plain words ("make the ask smaller", "lead with the cost", "move the vendor comparison to the appendix", "this audience is the CFO"). You edit draft.json and rebuild.

If the user wants a native Google Slides deck and `DD doctor` shows `google_oauth: true`, run `DD slides <project>` and give them the link. Otherwise tell them to upload the .pptx to Google Drive and open it with Google Slides.

## 7. Revise

Apply each requested change to draft.json and rebuild. Keep the user's wording when they rewrite a headline or claim. If their wording outruns the evidence, say which figure or source no longer supports it, and do not quietly rewrite the evidence to match. Add a `judgment` entry for each change of substance. A rebuild replaces `out/` and REVIEW.md, and the decision log keeps the record.

## Rules that do not bend

- Never invent a figure, result, quote, customer, or endorsement. Put a gap on the slide as a bracketed placeholder (`[Q4 budget, finance to confirm]`) and log it.
- Never settle a conflict between sources silently. Flag it in `conflicts`.
- Use only the Helix Bioworks brand and the bundled template.
- Report what was actually done. If a source could not be read, say so. The rule checks ran without a profile-specific rule classification, so say that too if the user asks how thoroughly the deck was checked.
