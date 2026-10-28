## ERROR HANDLING RULES

Retries
- Only retry errors that can succeed later: connection errors, timeouts, and HTTP 408/429/500/502/503/504/529.
- Never retry other 4xx errors (400, 401, 403, 404). Raise immediately.
- Never retry config errors (bad URL, missing key, invalid header) or SSL errors.
- Use exponential backoff and a fixed max attempt count.

Raising
- Raise the final error at the point you know you're out of retries. Don't raise inside a try block just to have your own except catch it.
- Don't call raise_for_status() twice on the same response.
- Always chain with `raise ... from exc` when wrapping an exception.
- Keep the useful detail in the final message: status code, the first ~200 chars of the response body, and attempt count. Don't overwrite it with str(exc).

Catching
- Catch specific exceptions, not bare `except` or `except Exception`.
- Don't let your own raised exceptions get caught by your own except block. Use a different exception type or raise outside the try.
- Validate response shape (missing keys, empty lists, None values) and raise one clear error instead of letting KeyError/IndexError/TypeError leak out.

Config
- Validate required settings (API key, base URL) at init and fail early with a clear message. Don't let an empty value turn into an auth error later.

Dead code
- Don't add unreachable fallback raises after a loop where every path already returns or raises.