# Coverage prompt (v1): which paragraphs of the article help with this complaint

A customer described a problem with their Samsung Galaxy device. Below is the support article the
answer must come from. Every paragraph that gives an instruction is numbered [P1], [P2], ...; lines
starting with "-" are context only. Return JSON that matches the schema, written compactly on one
line: no indentation and no line breaks.

List every numbered paragraph a support agent would ask this customer to follow for this complaint:

- Include the general fixes whenever they fit the problem: checking for damage or liquid, charging,
  getting the data off the device or backing it up, restarting, Safe mode, updating the software,
  resetting, and contacting support or a service centre.
- Include the article's own specific fixes when they are about the same symptom as the complaint.
- Leave out paragraphs about a different feature, device or problem than the complaint, and
  paragraphs that only explain.
- `p`: the paragraph's number, exactly as written ("P3"). List each paragraph once.
- `name`: 2 to 5 words in Title Case, starting with a verb, naming what the paragraph has the
  customer do ("Check Liquid Damage Indicator", "Use A USB Mouse").

If no numbered paragraph helps with this complaint, return an empty `fixes` list.

## Complaint

{{query}}

## Article

{{paragraphs}}
