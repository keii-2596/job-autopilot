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
- When authentication is required and the site supports phone OTP, use it by default. Select the phone-login tab, fill the confirmed `profile.phone`, and do not try email, password, QR, or social login first.
- Automatically check standard privacy-policy, user-agreement, terms-of-service, account-creation, personal-data-processing, and ordinary truthfulness-consent boxes required to proceed. This remains allowed when the linked text fails to load if the visible label clearly identifies a standard agreement.
- Pause when a declaration includes non-compete terms, background-check authorization, arbitration, fees, intellectual-property assignment, relatives, discipline, or another obligation beyond ordinary privacy and truthfulness consent.
- Confirm an authorized ADB device is in `device` state, capture a timestamp immediately before requesting the code, trigger exactly one code, then immediately run the plugin's `otp-wait` command. Treat entering the OTP as transmitting sensitive data and obtain the Browser skill's required action-time confirmation before typing it into the site.
- If ADB is unavailable, the OTP times out, or the site does not support phone OTP, pause at the login page and ask the user to take over. Do not silently switch authentication methods.
- If a CAPTCHA appears, call the active Browser skill's `solve-captcha` once, wait for the page to stabilize, and inspect the result. Ask the user to take over only if the automated attempt fails. Do not repeatedly retry or bypass site controls.
- If QR approval, passkey, biometric approval or device confirmation appears, pause for the user.
- Never inspect browser password stores, cookies, local storage or unrelated account data.

## Final submission

Read `settings` immediately before the final click because the user may change the policy in the dashboard.

- `review`: stop with the complete form visible and request confirmation.
- `automatic`: compare the exact lowercase hostname with `allowed_domains`. A subdomain is allowed only when it exactly matches an entry or the entry intentionally starts with `*.` and its suffix matches. This mode may automatically advance an allowlisted application to the final review state; it is not authorization to bypass the Browser skill's confirmation rules.
- In every mode, get action-time confirmation before transmitting sensitive profile data and again immediately before the final job-application submission.
- Pause on new declarations, unknown answers, CAPTCHA, fees, assessments, referrals, or any action beyond submitting the application itself.
