# Deck drafter: what it is, how I got here, and what I built

## What it is

Deck drafter is a Claude plugin that takes a pile of mixed material and returns a first-draft slide deck, with every editorial judgment written down for a person to review. You give Claude a folder of files, pasted emails, Google links, or screenshots. Claude reads all of it, decides what the deck is for, writes the story and the headlines, picks the evidence for each slide, and flags anything that conflicts or is missing. The plugin then checks those choices against the sources and builds an editable PowerPoint file, an HTML version with presenting and reading modes, and a one-page review of the decisions that need a human. It can also push the deck to native Google Slides.

The goal is to shorten the distance between a jumble of content and a draft a person can react to, without letting the tool make up facts or quietly settle disagreements between sources. Visual design is out of scope. The template is a plain stand-in, and the tool reports where its layouts fall short rather than forcing content into them. Everything runs on a fictional company, Helix Bioworks. It is a proof of concept, not a product.

## How I got here

I started with the question of what a good presentation editor actually knows. A separate pipeline distilled 17 books on presentations, data visualization, and business writing (Duarte, Minto, Knaflic, Reynolds, Alley, Doumont, Kosslyn, Mayer, and others) into candidate rules. I curated these into a knowledge base of 323 rules in eight categories: audience, narrative, claims, evidence, data, design, speaking and writing, and common failures. I merged 63 duplicates, resolved the places where the books contradict each other, and gave every rule a stable ID so a decision can be traced to the rule behind it. I then wrote a brand layer for Helix Bioworks, with 34 entries that can advise on, tighten, or override a knowledge-base rule. Every override is logged next to what the knowledge base alone would have done.

Over three days I built the engine around that knowledge with Claude Code. It has a deck profile and rule activation, a decision log with human review, a linter with 41 rule checks, and a layout manifest that acts as the contract between content and template. It also has a PowerPoint renderer, a content schema with provenance for every piece of evidence, and readers for Word, PDF, PowerPoint, email, CSV, and Google files. On top of those sit contradiction detection, a narrative stage with a human approval gate, composition, layout selection by the shape of the evidence, native Google Slides rendering with sync of human edits, an HTML version, and QA on the rendered slides.

Three things shaped the editorial logic along the way. A blind test on a 16-file synthetic package I prepared found eight real conflicts in the sources and carried all of them through to the deck instead of guessing. The decks were sound but not convincing, so I rebuilt the narrative as a persuasion argument, with the evidence as proof for each step. That added 44 persuasion rules drawn from a guide I had synthesized from 31 marketing books. Then I moved the persuasion into the headlines. The narrative now ends on a story (the problem, why the audience cares, why our answer is the best way forward) and a headline flow, and the evidence assertion becomes the subhead.

The engine worked, but only as a command-line tool on my machine. A full run took about a dozen commands, and every judgment step shelled out to the Claude command-line tool as a separate model call. The last two runs before today stopped halfway because that tool hit its usage limit. My brief always said the finished system should be a Claude plugin, with Claude as the interface. Today's work makes that real.

## The approach

The plugin splits the work by who should do it.

| Step | Done by |
|---|---|
| Read the sources, including images and scanned pages | The plugin reads files. Claude views and transcribes images |
| Decide the audience, the ask, the story, and the headline flow | Claude |
| Choose evidence for each slide, decide what to cut, flag conflicts and gaps | Claude |
| Check every quote, locator, and figure against the sources | The plugin |
| Choose each slide's layout from the shape of its evidence | The plugin |
| Split overflowing slides, paginate the objections, route large tables to the appendix | The plugin |
| Check the deck against the knowledge base and brand rules | The plugin |
| Render the PowerPoint, HTML, and Google Slides versions | The plugin |
| Decide what to keep, change, or overrule | The person |

The judgment happens in the Claude conversation the user already has open, so the engine makes no model calls of its own. Claude writes its decisions to one file, `draft.json`. That file holds the brief, the story, every piece of evidence with an exact quote and the line it came from, the slides, the objections, the appendix, the conflicts, what was excluded, and a log of each judgment call with its reason. A judgment guide inside the skill tells Claude how to make those calls. It is distilled from the engine's narrative and slide-copy stages and cites the knowledge-base rule behind each instruction.

The plugin then holds the draft to the sources. It looks for each quote in the source text and corrects a wrong line reference. A quote it cannot find is kept but marked unverified. It checks that every figure in the evidence appears in the source, and that every figure on a slide appears in the evidence that slide cites. A figure on a slide without a source is flagged. A figure written as a bracketed placeholder is accepted, because that is how a draft marks a gap. None of these problems are fixed silently, and none stop the build. They are listed in `REVIEW.md` for the person to decide. Only structural mistakes stop the build, such as an evidence ID that does not exist or an internal ID left in slide text. The skill tells Claude to fix its own errors before handing over and never to make a flag go away by weakening the evidence.

## What I built today

- A source intake that takes any mix of inputs. It reads Markdown, text, CSV, email, PDF, Word, PowerPoint, Excel, HTML, RTF, and JSON directly. It converts legacy Office and OpenDocument files through LibreOffice. It routes images and scanned PDFs to Claude for transcription, which then becomes a citable source. Pasted text and Google files enter through an inbox folder or by link. Source IDs stay stable when material is added later.
- A verification module for quotes, line references, and figures. It ignores product codes such as HX-212 and accepts a figure written as a word in the source ("five weeks") as matching a numeral on the slide ("5 weeks").
- A build step that reuses the engine's existing planner, fit-or-split logic, objections pagination, schema validation, linter, renderers, and decision log. It writes the review page.
- Two small additions to the engine, both compatible with the command-line tool. Run folders can live outside the repo. Rule activation can run without a model, keeping conditional rules in force rather than switching them off.
- The plugin itself: a manifest, a local marketplace, the `draft-deck` skill with its judgment guide, the draft format, a worked example, and a script that installs its own Python packages on first use. A packaging script copies the engine into the plugin so an installed copy does not depend on this repository.
- Eight new offline tests. The suite went from 348 to 356, all passing.
- A demo pile of ten files in eight formats: the generic programme fixture, plus pasted meeting notes, a budget spreadsheet, a whiteboard photo, a saved vendor web page, and a legacy Word file.

## What was tested

| Test | Result |
|---|---|
| Offline test suite | 356 tests pass |
| Demo pile, drafted by me in this session as the host Claude | All 10 files read. 13 slides plus 4 appendix pages, each claim slide drawn natively (trend chart, big number, scatter, mechanism diagram, flow, comparison). Two real conflicts between sources flagged. My own slips caught by the checks: a figure written as "180,000" where the source says "180k", semicolons the brand forbids, and a count I had derived myself. After fixes, every quote verified and no lint findings |
| Packaged plugin run from outside the repo | Same result, using only the engine copied into the plugin |
| Native Google Slides push from the plugin | 17-page Google Slides deck created |
| Fresh Claude Code session given the plugin and the demo pile, first attempt | Worked end to end in 52 seconds for $0.63. It noticed that the worked example bundled with the skill was written for the same pile, said so, and reused it. That proved the mechanics but not independent judgment, so I replaced the example with one written for a different pile |
| Fresh Claude Code session, second attempt, no matching example | Worked end to end in 3.5 minutes for $1.18. It wrote its own draft of 11 slides plus 5 appendix pages. It found both conflicts and flagged them rather than choosing. It kept the vendor's unchecked 6% hit rate out of the headlines and declined to extrapolate activity to 45 C from three points. It added bracketed placeholders for the final cost and for the effect on Q4 screening, and fixed its own flags before handing over. The final lint showed one warning and one advisory. One weakness: the headline "The cost is not final yet" announces an open item, which the judgment guide says a headline should not do |

| Published to GitHub and installed with `claude plugin marketplace add henrygwazda/deck-drafter` | Installed version 0.2.0 and ran on the engine bundled inside it |
| Simulated chat sandbox: read-only plugin folder, a bare Python without the packages, empty home folder | Packages installed in about 5 seconds, and all 10 sample files read and built into the same 13-slide deck |

Not tested yet: a real Cowork task, a real claude.ai chat, fetching a Google Doc through a connector, legacy PowerPoint and Excel conversion (only a legacy Word file was tested), and scanned PDFs.

## Limits

Rule activation runs without a model, so every conditional rule stays in force. The checks may therefore raise advisories that a profile-specific activation would have switched off.

The engine's model-based narrative QA and slide QA do not run in the plugin. Claude checks its own draft against the review page instead.

The narrative approval gate from the command-line tool becomes a quick draft followed by conversational revision. The narrative design allows this as its quick-draft mode, and the story and headline flow are still saved and shown first in the review.

Reading Google Slides edits back into the draft is built in the engine but is not yet wired into the plugin.

Claude must read every source in full, which will strain the context window on a very large pile. The largest pile tested so far has ten files.

## What comes next

This is a proof of concept. The slides are deliberately undesigned. The test template exists to show the structure the tool builds, and it is defined by a layout manifest that any template can implement. A designed template that implements the same manifest would receive the same structure without changes to the tool. Building it is the next step on the visual side, and the layout-gap reports show where it needs layouts the test template lacks.

The HTML pipeline is not finished. Today it renders the deck as one page with presenting and reading modes. The goal is a richer page built from the same source: the deck's argument with the supporting evidence and detail that a tight deck leaves out, woven in where a reader wants it. That page should stay in step with the deck as people edit it in Google Slides, so keeping it current needs no HTML knowledge. The page template and that sync make up version 2 of the pipeline. The engine already reads human edits back from Google Slides and flags changes that break the argument, so the sync has a foundation, but it is not yet connected to the plugin or to the HTML.

The repository includes a 13-file test package for a fictional product launch, put together separately from the tool. The plugin bundles it, so anyone can try the tool with `/deck-drafter:try`, or by asking for a demo in chat.
