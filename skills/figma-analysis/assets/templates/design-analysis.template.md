---
type: design-analysis
ticket: $TICKET_ID
version: $VERSION
title: Design analysis for $TICKET_ID
status: $STATUS
schema: 1
produced_by: artel:figma-analyst
---
# Design Analysis: $TICKET_ID

## Metadata

- **Figma link(s):** $FIGMA_URLS
- **Other Figma links (present, not analyzed):** $OTHER_LINKS
- **File key / root node:** $FILE_KEY / $ROOT_NODE_ID
- **Sections found:** $SECTIONS
- **Captured:** $CAPTURE_DATE

_(`status:` values: `DESIGN_ANALYZED`, or `DESIGN_BLOCKED` when a Major finding was parked for the designer.)_

## 1. Workflow Map

$WORKFLOW_MERMAID

_(mermaid flowchart: screens as nodes, connector names as edge labels — translated, original in
parentheses. One shared graph when desktop/mobile flows match; one graph per form factor when they
diverge. State-reference sections are listed below the graph, not drawn as flow.)_

### Transitions

| # | From | To | Trigger (translated) | Connector node |
|---|------|----|----------------------|----------------|
| 1 | $FROM | $TO | $TRIGGER | $NODE_ID |

## 2. Screen Inventory & Mapping

| Screen | Desktop node | Mobile node | Verdict | Codebase path / proposed location | Notes |
|--------|--------------|-------------|---------|-----------------------------------|-------|
| $SCREEN | $D_NODE | $M_NODE | $VERDICT | $PATH | $NOTES |

Verdicts: `exists as-is` / `needs modification` / `new screen`.

## 3. Fit Into Existing Logic

$FIT

_(How the flow attaches to the host project's routing and state-management surfaces, per its
conventions docs; which entry points trigger it.)_

## 4. Desktop vs Mobile Differences

| # | Difference | Implementation implication |
|---|------------|----------------------------|
| 1 | $DIFF | $IMPLICATION |

## 5. Discrepancies

### Major

- **M1:** $FINDING
  - **Proposed correction:** $CORRECTION
  - **Resolution:** $RESOLUTION

_(Resolution is recorded from the user handshake: proceed as designed / apply correction / park for
designer.)_

### Minor

- **m1:** $FINDING

_(Minor findings feed the analysis interview — they are questions for the PRD, not blockers.)_

## 6. Evidence

- ![$LABEL](design/$FILE.png)

## 7. Deep-Pull Reference

| Screen | Form factor | Node ID | Deep link |
|--------|-------------|---------|-----------|
| $SCREEN | $FORM | $NODE_ID | $URL |

_(For the implementer: run the connected server's design-context tool, e.g. `get_design_context`,
on these nodes on demand — layout specs are deliberately not recorded here.)_
