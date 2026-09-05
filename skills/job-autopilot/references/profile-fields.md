# Applicant profile fields

The dashboard stores a small local profile. Common reusable fields are:

- `name`, `phone`, `email`, `city`
- `school`, `degree`, `major`, `graduation_year`
- `resume_path`, `portfolio_url`, `github_url`
- `preferred_locations`, `preferred_roles`
- `work_authorization`, `earliest_start_date`
- `notes`

Only store facts the user explicitly provides. Passwords, one-time codes, identity-card numbers, bank details and raw SMS messages must never be stored in the profile.

The profile also contains a `custom_fields` array. Each confirmed entry has:

- `key`: stable English identifier for the concept
- `label`: user-facing semantic name
- `value`: the confirmed reusable fact
- `aliases`: actual question phrasings observed on recruiting sites
- `scope`: `global` or a narrow company/site scope
- `source` and `confirmed_at`: provenance for later review

Match form questions semantically, not by exact strings alone. Consider the label, nearby help text, available choices, form section, company, role, time range, and negation. Similar-looking questions are not necessarily equivalent. When uncertain, ask the user and show the proposed interpretation before storing anything.

For a new confirmed fact, use `profile-field-set` and save the site's phrasing as an alias. If an answer is specific to one company or application, use a narrow scope instead of making it globally reusable. Passwords, OTPs and financial credentials are forbidden; high-sensitivity identity values must not be automatically reused across sites.
