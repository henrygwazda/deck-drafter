# draft.json

Write this file at `<project>/draft.json`. The build reads it, checks it against the sources, picks layouts, and renders the deck. A complete example sits next to this file in `example_draft.json`.

## Top level

```json
{
  "deck_id": "screening-q4",
  "brief": {...},
  "story": {...},
  "evidence": [...],
  "sections": [...],
  "title": {"headline": "", "subhead": "", "notes": ""},
  "exec_summary": {"headline": "", "points": ["", "", ""], "open_note": "", "read": "", "notes": ""},
  "slides": [...],
  "risks": {"headline": "", "after_section": "SEC02", "notes": "", "items": [...]},
  "closing": {"headline": "", "ask": "", "notes": ""},
  "appendix": [...],
  "conflicts": [...],
  "excluded": [...],
  "judgment": [...],
  "positioning_headline": ""
}
```

`deck_id` is optional and defaults to the project folder name. `sections`, `risks`, `appendix`, `conflicts`, `excluded`, `judgment`, and `positioning_headline` are optional.

## brief

```json
{
  "audience": "Programme steering group: senior scientists and the finance lead",
  "decision_maker": "Steering group chair, accountable for the Q4 R&D budget",
  "purpose": "Get the Q4 validation run funded",
  "ask": "Approve the 45 C validation run for HX-212 in Q4",
  "deck_type": "proposal",
  "channels": ["internal"],
  "delivery": "live",
  "time_minutes": 20,
  "expertise": "expert",
  "disposition": "evaluating",
  "assumptions": ["Delivery is live at the Q4 steering meeting; nobody said so"]
}
```

`deck_type` is one of `fundraising`, `update`, `proposal`, `briefing`, `report`, or `other`. `delivery` is `live`, `read`, or `both`. `channels` uses `investor`, `customer`, `partner`, `internal`, or `collateral`. An `investor` or `collateral` channel adds the Helix positioning slide, word for word from the brand file. `positioning_headline` sets that slide's headline. `expertise` is `expert`, `mixed`, or `general`. `disposition` is `supportive`, `evaluating`, `skeptical`, or `unaware`.

## story

```json
{"problem": "", "why_care": "", "solution": "", "why_best": "", "thesis": ""}
```

`thesis` is required. The others shape the headline flow and appear in REVIEW.md.

## evidence

One entry per fact you use. Every entry cites a readable source by id and the marker of the line it came from, and quotes the words exactly.

```json
{"id": "EV01", "source": "SRC04", "locator": "L11",
 "quote": "rose from 4% in Q1 to 7% in Q2 and 9% in Q3",
 "kind": "chart", "pattern": "temporal_trend",
 "text_live": "Hit rate 4% to 9% in three quarters",
 "text_read": "Hit rate per screened library rose from 4% in Q1 to 7% in Q2 and 9% in Q3.",
 "verification": "source-supported",
 "chart": {"type": "line", "title": "Hit rate per library", "unit": "%",
           "categories": ["Q1", "Q2", "Q3"], "series": [{"name": "Hit rate", "values": [4, 7, 9]}]}}
```

- **`locator`** is the marker at the start of the line in `sources_text/SRCnn.txt`: `L12`, `p3 L7` (PDF), `P4` (Word paragraph), `T1 row 2`, `S3 B2` (deck body), `S3 notes`, `body L6` (email), `row 4` (CSV), `X1 row 3` (spreadsheet). Use a range such as `L4-L6` when the quote spans lines. The build finds the quote and corrects a wrong locator. It logs the correction.
- **`quote`** is copied exactly, at least a few words, without the marker. Do not paraphrase. A quote the build cannot find is marked unverified and flagged for review.
- **`verification`** is `source-supported` (stated directly), `derived` (you computed it from stated values, with the arithmetic in `transform`, for example `"transform": "105/1500 = 7%"`), `interpreted` (a reading of what the source says), or `unverified`.
- **`kind`** and its payload, which decide the layout:

| kind | payload | becomes |
|---|---|---|
| `big_number` | `"value": "9%"`, `"label": "Q3 hit rate"` (under eight words) | big number slide. With a quote from a different source on the same slide, a figure-plus-quote slide |
| `chart` | `"chart": {"type": "bar"\|"line", "title", "unit", "categories": [...], "series": [{"name", "values": [...]}]}`. For scatter use `{"type": "scatter", "x_label", "y_label", "series": [{"name", "points": [[x, y], ...]}]}` with at least 3 points | trend chart (time categories or line), bar chart, or scatter |
| `comparison` | `"comparison": {"left": {"label", "text"}, "right": {"label", "text"}}` | two-column comparison |
| `diagram` | `"diagram": {"type": "flow", "steps": [...]}`, or `{"type": "mechanism", "nodes": [{"id", "label"}], "edges": [{"from", "to", "kind": "causes"\|"inhibits"\|"feedback", "label"}]}` | flow or mechanism diagram |
| `table` | `"table": {"title", "columns": [...], "rows": [[...]]}` | live table up to four rows, otherwise the appendix |
| `text` | none | a three-point text slide when nothing more structured anchors the slide |

Every number in a payload must appear in the source. Payloads with numbers the source does not state are removed and the item is kept as text. **Tables and charts the intake report lists as "copied cell for cell"** (a CSV, a Word or Markdown table, a spreadsheet sheet, a native PowerPoint chart) should be cited with their source and exact locator (`"locator": "T1"`, `"rows 1-6"`, `"X1"`, `"S2 C1"`) and `"kind": "table"` or `"chart"`. The build then copies the data from the file. For those you may leave the payload and quote out. Give a table its `unit` (for example `"unit": "dollars"` or `"unit": "variants and hits, counted"`) so it stands alone (DAT-04).

`pattern` is optional. Use it when the shape is ambiguous. Values: `comparison`, `process_sequence`, `temporal_trend`, `distribution`, `relationship`, `scientific_mechanism`, `quantitative_result`, `evidence_matrix`, `risk_register`, `mixed_evidence`, `contextual_explanation`.

`exec_summary.read` is the summary page's text in the read version (100 to 250 words). Without it the build joins the headline, points, and open note.

## sections

```json
{"id": "SEC01", "title": "What the screen is finding", "question": "Is the platform working?", "summary": "Hit rate has more than doubled."}
```

Divider slides appear when there are two or more sections.

## slides

The claim slides, in deck order. The build adds the title, executive summary, dividers, risks pages, and closing around them.

```json
{"section": "SEC01", "job": "proof",
 "headline": "Your screen is now finding more winners",
 "subhead": "Hit rate per library rose from 4% in Q1 to 9% in Q3 across 34 libraries.",
 "claim": "The platform's hit rate has more than doubled over three quarters.",
 "evidence": ["EV01", "EV02"], "show": "EV01",
 "points": ["Same assay and host strain all year", "Q3 drew on the largest library set"],
 "read": "100 to 200 words for the read version",
 "notes": "60 to 120 words of talk track"}
```

`job` is one of `problem`, `stakes`, `reframe`, `answer`, `why_best`, `proof`, `objection`, `limit`, or `plan`. `show` is the evidence the slide displays. The others land in the notes. If the build cannot draw `show`, it picks the best drawable evidence and logs why.

**Space.** Headlines run up to about 59 characters and subheads up to 200. A slide whose only evidence is text gets exactly three points of at most ten words each. Other slides get at most two points of at most twelve words. When a slide overflows, the build moves trailing points to a continuation slide and logs the split. Put a limit that qualifies the slide's claim in its points and in `read`.

## risks

```json
{"headline": "The questions that would stop a yes, answered",
 "after_section": "SEC02",
 "items": [{"text": "Will HX-212 hold activity at 45 C?", "response": "Unknown: activity falls from 3.1 to 2.4 by 40 C", "evidence": ["EV05"]}]}
```

The objection runs to at most twelve words and the response to at most fifteen. Rank the items, biggest first. Pages split automatically at five rows or about 55 words. `after_section` places the page after that section. Leave it out to place the page just before the closing.

## appendix, conflicts, excluded, judgment

```json
"appendix": [{"title": "Full screening results by quarter", "evidence": ["EV03"], "why": "Reviewers will ask for the per-quarter counts"}],
"conflicts": [{"evidence": ["EV07", "EV09"], "note": "The review says 140 hits in Q3, the deck chart says 136", "handling": "The review's figure is used and both appear in the notes"}],
"excluded": [{"source": "SRC01", "why": "Build script for the fixture, not content"}, {"evidence": "EV12", "why": "Duplicates EV03"}],
"judgment": [{"type": "assumption", "target": "brief", "decision": "Treated as a live steering-group meeting", "why": "The email refers to the steering group deciding", "rules": [], "review": true}]
```

## Checks the build runs

The build stops with a list under `fix_these` for structural problems: missing fields, unknown ids, empty quotes, or internal ids in visible text. Fix them and rebuild. It does not stop for quotes it cannot find, figures not in a source, figures on a slide that are not in the cited evidence, conflicts, or layout gaps. Those are flagged in REVIEW.md for the user.
