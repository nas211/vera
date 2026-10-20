# WHY AI help desk

The global Help desk button and `/help` connect to a real, text-only instance of
Cybella's **Customer Support** agent. Its role expertise is reused verbatim from
`cybella-ai/cybella-app`, `services/core/cybella_core/personas.py`, commit
`0342cc36e43ea2073d5be2b9c76e96fbcc7a2307` (staging).
This is an adaptation hosted by WHY, not a connection to a customer's Cybella
tenant or the retired `aoc/` assistant. No agent tools or account access are
enabled. The model is independent of the trading execution system.

`lib/helpdesk/policy.ts` holds the support role and approved WHY knowledge from
the dashboard and onboarding. Keep it in sync when connection/start controls,
plans or supported exchanges change. The chat cites guide names; the UI provides
fixed official links. The AI does not send its conversation to anyone. The
**Need a person? Chat with Nad** button opens a separate human conversation:
the visitor's messages go to Nad on Telegram, and his replies return to the
same website chat. See `services/human-support/README.md` for operation and tests.

The credit is `Powered by Cybella AI → https://www.cybella.io`, using the
company's proper name and working website as confirmed by the owner.

## Runtime

Server-only variables (never `NEXT_PUBLIC_`):

- `WHY_SUPPORT_ENDPOINT`: full HTTPS chat-completions URL.
- `WHY_SUPPORT_MODEL`: configured supported model ID.
- `WHY_SUPPORT_REASONING_EFFORT`: provider-supported effort (deployed: `low`).
- `WHY_SUPPORT_API_KEY`: inference API credential only; no exchange credentials.
- `WHY_SUPPORT_ORIGIN`: optional comma-separated allowed browser origins.
  Defaults to `https://whytradingbot.com,https://www.whytradingbot.com`.

The deployed instance uses WHY's existing Groq inference account with
`openai/gpt-oss-120b`, a 1,200-token completion ceiling (including reasoning)
and a 25-second provider timeout. Reasoning is excluded from the displayed reply.
Qwen was tested but its separate daily model quota was already nearly exhausted
by existing workloads, so support uses the available GPT-OSS model allowance.
The website service loads the values from its protected environment file.
Inference uses the compatible chat-completions protocol; there is no fallback
that pretends an unavailable model produced an answer.

The POST handler validates origin, a 16 KiB body limit, an 800-character question,
last eight user/assistant turns, and checks for commonly pasted credentials on
both client and server. Credential detection is a heuristic, not a guarantee;
the UI explicitly asks users to keep credentials out of chat. No conversations
or provider bodies are logged by the application. Messages remain in the current
page's memory; closing/reopening retains them, reloading or New chat clears them.
This applies to AI chat. Human support messages are stored separately for 30
days and can be resumed through a Secure, HttpOnly support-session cookie.
Question and recent conversation are sent to the inference provider for answers.

Nginx overwrites `X-Real-IP`; the handler hashes that address for an in-memory
limit: five requests/minute, 40/day per visitor, one concurrent request per
visitor, four concurrent requests globally, 1,000/day globally. Counters reset
on website restart and are per process. Before scaling beyond the current single
process, move counters to shared storage. Provider failures consume a request.

## Checks

`node --test tests/helpdesk-api.cjs` uses mocked inference to verify grounding,
input and origin validation, credential blocking, honest provider failures,
bounded history, reasoning removal and throttling.

`HELPDESK_TEST_URL=http://127.0.0.1:3014 PLAYWRIGHT_MODULE_PATH=/root/browser-control/node_modules/playwright CHROME_PATH=/usr/bin/google-chrome node --test tests/helpdesk-browser.cjs`
checks phone/desktop chat, follow-ups, source/credit links, retry, reset, literal
reply rendering and pre-transmission credential blocking. Inference is mocked.
Also run the existing profile live-flow browser suite and the production build.
