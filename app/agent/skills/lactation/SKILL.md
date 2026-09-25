---
name: lactation
description: Provides professional support for common lactation and milk-supply concerns. Load this Skill when the user raises issues such as milk-supply concerns, infant growth or intake concerns, latch difficulties, nipple pain, pumping discomfort, breast engorgement, firmness, or pain.
---

# Lactation Skill

This is the entry point for professional lactation support.

Use the user's full request, the current context, and their actual needs to identify the issue they most want to address now, then load the best-matching reference file.

The reference files provide professional assessment frameworks and general approaches for their respective situations, not fixed answers. Tailor your judgment and advice to the user's circumstances, goals, practical constraints, previous attempts, and current request.

## Principles for Use

- Identify the user's **main concern** first and prioritize loading one primary reference file.
- Route based on the full meaning of the request, the setting in which the issue occurs, and the user's goals; do not match a single keyword mechanically.
- Load another reference only if a sound assessment genuinely depends on another professional dimension. Do not load every potentially related file at once.
- If the user's goal changes, switch or add references as needed.

## Reference Files

### `milk-supply-assessment.md` — Assessing Milk Supply

Load when the main question is **whether milk supply is sufficient or has changed unexpectedly**, for example:

- Is my milk supply enough?
- Has my supply dropped?
- I recently pump less milk; does that mean my milk is drying up?
- Is my milk supply normal?

If the assessment requires a closer look at whether the baby is getting enough milk or growing as expected, load `infant-intake-assessment.md` or `infant-growth-assessment.md` as needed.

---

### `milk-supply-management.md` — Adjusting and Managing Milk Supply

Load when the user has clearly said she wants to **increase, maintain, or reduce milk supply** and the main question is what to do next, for example:

- How can I increase my supply?
- How can I maintain it?
- I make too much milk; how can I reduce it?
- How should I adjust breastfeeding or pumping?

If she first needs to establish whether there is actually a supply problem, load `milk-supply-assessment.md` first.

---

### `infant-intake-assessment.md` — Assessing Infant Intake

Load when the main question is **whether the baby is actually getting enough milk**, for example:

- Is my baby getting enough to eat?
- How much should the baby have at each feed?
- The baby cries after a feed; does that mean they did not get enough?
- Do I need to supplement?

This file may also support a milk-supply assessment when the baby's actual intake needs to be considered.

---

### `infant-growth-assessment.md` — Assessing Infant Growth

Load when the main question is **whether the baby's weight or growth trend is as expected**, for example:

- Is my baby's weight gain normal?
- What should I do if the baby has gained weight slowly recently?
- Does weight gain show whether my milk supply is enough?

This file may also support a milk-supply assessment when longer-term intake needs to be considered.

---

### `latch-and-nipple-pain.md` — Latch and Nipple Pain

Load when the main issue occurs **during direct breastfeeding**, for example:

- Difficulty latching or a shallow latch;
- The baby repeatedly slips off the breast;
- Nipple pain during breastfeeding;
- Nipple damage, distortion, or significant discomfort.

If the issue mainly occurs while using a breast pump, load `pumping-support.md`. If it is primarily breast fullness, a lump, or pain in the breast itself, load `breast-symptoms.md`.

---

### `pumping-support.md` — Pumping Discomfort and Effectiveness

Load when the main issue occurs **while using a breast pump**, for example:

- Pain, friction, or pulling during pumping;
- Nipple swelling;
- Suspected flange-fit or suction problems;
- Poor pumping efficiency or a feeling that milk remains in the breast.

If the main question is whether overall milk supply is truly low, load `milk-supply-assessment.md` as needed.

---

### `breast-symptoms.md` — Breast Fullness, Lumps, and Pain

Load when the main concern is **fullness, firmness, a lump, tenderness, or pain in the breast itself**.

Address the breast symptoms first rather than starting with a complete milk-supply assessment.

If there is also fever, marked redness, systemic illness, or another possible medical risk, follow the shared safety guidelines first.

---

### `return-to-work-feeding.md` — Feeding and Lactation After Returning to Work

Load when the main question is **how to continue breastfeeding after returning to work or while working**, for example:

- How to arrange pumping during work;
- How to combine breastfeeding, bottle-feeding, and pumping;
- How to maintain supply after returning to work;
- How to arrange feeds when another caregiver looks after the baby during the day;
- How to plan milk storage, transport, and the workday feeding routine.

If there is also a specific concern about supply, pumping discomfort, or infant intake, load the corresponding reference as needed.

## Combining References

A user's question may involve several professional dimensions. Address the main concern first, then load another reference only when it is needed for the assessment.

For example:

- “I have been pumping less and less, and my baby is gaining weight slowly.”
  → `milk-supply-assessment.md` + `infant-growth-assessment.md`

- “My nipples really hurt when the baby feeds, and I worry they are not getting enough.”
  → `latch-and-nipple-pain.md` + `infant-intake-assessment.md`

- “Since going back to work I pump less and less. How should I adjust?”
  → `return-to-work-feeding.md`, followed as needed by `milk-supply-assessment.md` or `milk-supply-management.md`

## Safety and Behavior

- If you identify an emergency medical risk, self-harm risk, or risk of harm to the baby or others, follow the shared safety guidelines first.
- Follow the shared behavioral guidelines throughout the service.
