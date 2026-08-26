# Quality Inspection Procedure

**Document type:** Standard Operating Procedure
**Applies to:** All production lines
**Owner:** Quality Assurance Department

## Purpose

This procedure defines how in-process quality inspection is performed, how
defects are categorized and logged, and when a rejection rate must be
escalated for investigation.

## Inspection Frequency

Every unit produced is subject to in-process inspection at the end of its
shift's production run. Inspection results are logged per machine, per
shift, per date, and include inspected quantity, rejected quantity, and (for
rejected units) a defect type and description.

## Defect Categories

Inspectors classify each rejected unit into one of the standard defect
categories:

- **Dimension Out of Tolerance:** A measured dimension falls outside the
  spec tolerance band. Often linked to tooling wear, calibration drift, or
  fixture misalignment.
- **Surface Defect:** A visible surface blemish, scratch, or finish defect.
  Often linked to material handling, paint/finish equipment condition, or
  contamination.
- **Assembly Defect:** A component is misaligned, missing, or incompletely
  assembled. Often linked to assembly equipment condition - misalignment,
  positioning drift, or mechanical instability in the assembly machine
  (including issues originating in that machine's drive motor or
  actuators) can all present as an increase in assembly defects, since an
  unstable or underperforming assembly mechanism is more likely to place
  or join components incorrectly.

Inspectors should record the specific defect type observed, not a generic
"defective" label - the defect type is what allows quality data to be
correlated with an equipment or process cause later.

## Normal Rejection Rates

Baseline rejection rates vary by line and process, but a well-controlled
line typically runs in the low single digits (roughly 0.5%-3%) on a normal
day. A rejection rate meaningfully above a line's own recent historical
average - not an arbitrary fixed number - is the appropriate signal to
investigate, since normal rates differ between lines and processes.

## Escalation Thresholds

- A shift's rejection rate more than roughly double its trailing average
  should be flagged for investigation before the next shift begins.
- If a single defect type accounts for the large majority of a day's
  rejects, the investigation should focus on that defect type's typical
  causes (see Defect Categories above) rather than treating it as a
  general quality dip across the whole line.
- Quality Assurance should not attempt to diagnose the equipment cause
  directly - that is a maintenance function - but should flag which
  machine(s) and shift(s) the elevated rejects are concentrated on, since a
  defect spike concentrated on a single machine (rather than spread evenly
  across a line's machines) points toward an equipment-specific cause
  rather than a process-wide one.

## Correlating Quality with Equipment Condition

When investigating an elevated rejection rate:

1. Identify which specific machine(s) and shift(s) the rejects are
   concentrated on - do not assume the whole line is affected equally.
2. Identify the dominant defect type. An Assembly Defect spike in
   particular warrants checking that machine's maintenance history for any
   recent or ongoing mechanical issue, since assembly equipment condition
   is a common contributor to assembly-defect rates (see Defect
   Categories above).
3. Check whether the affected machine also shows reduced production output
   or elevated downtime over the same period - a quality issue and a
   production/downtime issue on the same machine around the same time are
   often connected rather than coincidental, and should be investigated
   together rather than in isolation.
4. Document the finding (rate, dominant defect type, affected
   machine/shift) even if the underlying equipment cause requires a
   separate maintenance investigation to confirm.

## Logging Requirements

Every inspection record must include inspected quantity and rejected
quantity for that machine/shift/date, even when zero units were rejected.
Records with rejects must include a defect type and a description specific
enough to be useful later (not "bad part" or "reject").
