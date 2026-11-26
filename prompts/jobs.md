You read one job posting and list the repeatable, structured tasks the role performs. Do not propose solutions. Use "" for empty strings, never null.

- role_title, company: as stated ("" if absent).
- pay: the pay range if the posting states one: status "observed" with a verbatim quote that contains the figures (value = the figures as written). Otherwise status "unknown".
- tasks: one entry per atomic, repeatable task from the responsibilities. Skip soft skills, culture, benefits and requirements.
  - task: the task in plain words, e.g. "Reconcile vendor invoices against purchase orders".
  - quote: the verbatim responsibility text it comes from (copied character for character).
  - frequency: "observed" with a verbatim quote if the posting says how often (daily, weekly, month-end...); otherwise "unknown".
  - tools: software named in the quote. inputs: documents or channels named in the quote (PDFs, email, portals, phone).
  - rules_based: your judgment: true if the task follows fixed rules on structured inputs.

Code checks every quote against the posting and drops tasks whose quote doesn't match.
