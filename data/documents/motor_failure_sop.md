# Motor Failure Standard Operating Procedure (SOP)

**Document type:** Standard Operating Procedure
**Applies to:** All motor-driven production equipment (conveyor assemblers,
pumps, drive motors, fans)
**Owner:** Plant Maintenance Department

## Purpose

This SOP defines the required response when a motor failure is suspected or
confirmed on production equipment, and describes the typical progression of
motor failures so technicians can recognize early warning signs.

## Recognizing a Motor Failure

Symptoms that should trigger this SOP include any of the following:

- Motor running noticeably hotter than its normal operating temperature
- Unusual noise (grinding, whining, knocking) from the motor or drive
  assembly
- Excessive vibration at the motor housing or coupling
- Reduced output or intermittent stalling under normal load
- Visible smoke, burning smell, or discoloration on the motor housing or
  winding
- Tripped overload protection or repeated nuisance trips

## Immediate Response

1. **Isolate and lock out the machine** before any inspection. Do not
   attempt to run a motor that is smoking, has tripped on overload more
   than once, or is producing a burning smell.
2. **Do not restart** the equipment until a technician has completed a
   diagnostic check, even if the fault appears to clear on its own.
3. **Log the event immediately** with the observed symptoms, the shift and
   time it was noticed, and who reported it - even before the diagnosis is
   complete. An initial "Inspection" record can be upgraded to "Corrective"
   once the cause is identified.

## Diagnostic Steps

1. Check the motor's recent temperature trend, if logged. A gradual upward
   trend over several inspections is more concerning than a single elevated
   reading, and should not be dismissed as noise.
2. Perform an insulation resistance test on the winding if winding failure
   is suspected.
3. Inspect bearings for play, roughness, or unusual heat - bearing wear is
   one of the most common precursors to a full motor failure and can often
   be caught before it escalates.
4. Check mounting and coupling alignment; misalignment accelerates bearing
   and winding wear.
5. Review the machine's maintenance history for prior motor-related events.
   Motor failures frequently do not appear out of nowhere - they often
   follow a **progression**: early overheating or a minor bearing issue,
   followed weeks later by a more serious bearing failure if the earlier
   signs were only monitored rather than corrected, and finally a winding
   failure if the degraded bearing condition is left unaddressed. A history
   of multiple motor-related corrective events on the same machine should
   be treated as an escalating pattern, not a series of unrelated
   incidents.

## Repair vs. Replace

- Overheating caused by a blocked cooling path or dirty fan can usually be
  resolved without parts replacement.
- Bearing wear or failure requires bearing replacement; running a motor
  with a known bad bearing risks progressing to winding damage.
- Winding failure (insulation breakdown, shorted or open windings) requires
  motor replacement or a full rewind; it cannot be resolved by
  adjustment alone.
- If a machine has had more than one motor-related corrective event in the
  past 90 days, replacement of the full motor assembly (not just the
  specific failed part) should be considered even if the current fault is
  repairable, to avoid another near-term failure.

## Post-Repair Verification

After any motor repair, before returning the machine to normal production:

- Run the machine unloaded and confirm normal temperature and vibration.
- Run at least one full shift under load with more frequent temperature
  checks than normal.
- Record the repair and downtime in the maintenance log with a specific
  `failure_type` (e.g., a defined motor-failure category), not a generic
  description.

## Preventive Recommendations

- Do not close out an "elevated temperature, flagged for monitoring"
  inspection without a scheduled follow-up check - monitoring only helps if
  the follow-up actually happens.
- Track motor-related failure types over time per machine; repeated entries
  of the same category are the strongest available signal of a developing
  problem and should trigger the escalation procedure described in the
  Machine Maintenance Manual.
- When investigating a production drop or a quality defect spike on a
  motor-driven machine, always check whether that machine has an open or
  recent motor-related inspection flag - a mechanical issue often affects
  output and quality before it causes a full stoppage.
