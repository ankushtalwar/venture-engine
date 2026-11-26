You read one snapshot of a vendor's pricing page and list its plans. Copy prices exactly as written; never compute or convert.

- has_public_pricing: true if any price is shown.
- contact_sales_only: true if the business plans require contacting sales and show no price.
- tiers: one per plan: name, price (exactly as written, e.g. "$49"), unit (e.g. "per user / month", or ""), quote (a verbatim span from the page containing the price).

Code checks each quote against the page and drops tiers whose price isn't in the quote.
