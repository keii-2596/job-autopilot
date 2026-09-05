# Browser application workflow

Use this reference only after reading the active Browser or Chrome skill.

## Select and prepare the browser

1. Use the browser surface selected by the Browser skill. Prefer an existing signed-in browser when the skill permits it.
2. Read that browser's complete runtime documentation before the first interaction.
3. Open only the official `job.url` stored in the ledger.
4. Inspect the page before clicking. Confirm company and role match the ledger entry.

## Reliable form filling

Work section by section instead of filling the entire page blindly:

1. Account and phone verification
2. Contact information
3. Education
4. Experience and projects
5. Resume and attachments
6. Screening questions
7. Consent and final review

After each section, inspect the visible values and validation errors. Use labels, roles and visible text rather than brittle coordinates. Reinspect after navigation, modal changes, uploads or dynamic form updates.

## Evidence and recovery

- Update the ledger before opening the site and after every durable state transition.
- Preserve the current URL and non-sensitive error summary when blocked.
- A screenshot may be stored as a confirmation reference only when it does not expose OTPs, identity numbers or unrelated private messages.
- Never mark an application submitted merely because the submit button was clicked. Require a success message, confirmation page, application ID, or an application visible in the site's application history.

## Authentication

- Reuse an existing authenticated browser session when available.
- When phone OTP is required, trigger one code, then use the plugin's `otp-wait` command with a timestamp captured immediately before the request. Treat entering the OTP as transmitting sensitive data and obtain the Browser skill's required action-time confirmation before typing it into the site.
- If CAPTCHA, QR approval, passkey, biometric approval or device confirmation appears, pause for the user.
- Never inspect browser password stores, cookies, local storage or unrelated account data.

## Final submission

Read `settings` immediately before the final click because the user may change the policy in the dashboard.

- `review`: stop with the complete form visible and request confirmation.
- `automatic`: compare the exact lowercase hostname with `allowed_domains`. A subdomain is allowed only when it exactly matches an entry or the entry intentionally starts with `*.` and its suffix matches. This mode may automatically advance an allowlisted application to the final review state; it is not authorization to bypass the Browser skill's confirmation rules.
- In every mode, get action-time confirmation before transmitting sensitive profile data and again immediately before the final job-application submission.
- Pause on new declarations, unknown answers, CAPTCHA, fees, assessments, referrals, or any action beyond submitting the application itself.
