Retries
- Only retry errors that can succeed later: connection errors, timeouts, and HTTP 408/429/500/502/503/504/529.
- Never retry other 4xx errors (400, 401, 403, 404). Raise immediately.
- Never retry config errors (bad URL, missing key, invalid header) or SSL errors.
- Use exponential backoff with jitter and a fixed max attempt count.
- On 429, honor the Retry-After header if present.
- Set an explicit timeout on every outbound request. No call should be able to hang forever.

Order safety
- Never blindly retry order submission. A timeout can mean the order went through. Send a client_order_id with every order, and before retrying, look the order up by that id to see whether it already exists.
- Reads (account, positions, quotes, news, macro data) can be retried freely.

Raising
- Raise the final error at the point you know you're out of retries. Don't raise inside a try block just to have your own except catch it.
- Don't call raise_for_status() twice on the same response.
- Always chain with `raise ... from exc` when wrapping an exception.
- Keep the useful detail in the final message: status code, the first ~200 chars of the response body, and attempt count. Don't overwrite it with str(exc).

Catching
- Catch specific exceptions, not bare `except` or `except Exception`.
- Don't let your own raised exceptions get caught by your own except block. Use a different exception type or raise outside the try.
- Validate response shape (missing keys, empty lists, None values) and raise one clear error instead of letting KeyError/IndexError/TypeError leak out.
- Don't swallow errors in the safety checks (daily loss limit, drawdown, position limits). If one can't run, treat it as failed.

Fail closed
- If anything upstream breaks (LLM call fails, response isn't valid JSON, market data is missing or stale, Alpaca is unreachable), skip the trade for that cycle and alert. Never fall back to a default action or guessed values.

Logging
- Log each retry with the attempt number, the status code, and the wait time.
- Never log API keys, secrets, or full auth headers, including inside exception messages and response dumps.

Docstrings
- Keep the first line of the docstring as a short, complete summary of what the module/class does (e.g. "Alpaca broker integration: account, orders, clock, reconcile, bracket orders.").
- If there's additional functionality, add it as a second paragraph separated by a blank line. Do NOT change the first line.

Config
- Validate required settings (API key, base URL) at init and fail early with a clear message. Don't let an empty value turn into an auth error later.

Dead code
- Don't add unreachable fallback raises after a loop where every path already returns or raises.

Process
- Read the files you'll touch and their callers first. Reuse existing helpers and match the existing style.
- Change only what the task needs. No unrelated refactors, extra features, or new config options.
- If the request conflicts with an existing rule or design decision, say so before writing code.

Accuracy
- Never invent an endpoint, parameter, field name, or library function. Check the docs or the installed package. If you can't verify something, say so.
- Don't claim it works unless you ran it or a test covers it.
- Type hints on all function signatures.

Config and secrets
- Nothing tunable is hard coded. Settings go in config.yaml, secrets in .env.
- Never log or commit a secret. Update .env.example when you add a variable.

Trading logic
- No floats for money or order sizing. Use Decimal or round to the broker's precision.
- Timestamps are timezone-aware UTC. Use the broker's clock endpoint for market hours.
- Mode (paper or live) is set in one place and logged at startup.
- Every order goes through the risk checks. No path skips them.
- Read positions and open orders from the broker. Don't trust local state.
- Treat LLM output as untrusted. Parse it into a strict schema and reject anything invalid (ticker not in watchlist, size over limits, bad side).

Testing and logging
- New logic gets a test, and bug fixes get a test that fails without the fix. Cover failure paths: timeouts, 429s, malformed LLM output, missing fields.
- Mock all network calls. Tests never hit a live API or use real keys.
- Run the full suite before calling it done.
- Use logging, not print(). Log every decision: what was proposed, what passed or failed risk checks, and what was submitted.

Finishing
- End with a short summary: what changed, how you tested it, and anything unverified.
- If something is unclear or risky, ask before guessing.
