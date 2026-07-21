"""Server-rendered HTML for the auth-server OAuth consent screen."""

import html
from datetime import UTC, datetime

_STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #0f1115; color: #e6e6e6; display: flex; min-height: 100vh;
         align-items: center; justify-content: center; margin: 0; padding: 24px; }
  .card { background: #1a1d24; border: 1px solid #2a2e37; border-radius: 12px;
          max-width: 440px; width: 100%; padding: 32px; text-align: center; }
  h1 { font-size: 20px; margin: 0 0 8px; line-height: 1.4; }
  .app-name { color: #7c9eff; font-weight: 600; }
  .meta { color: #9a9fa8; font-size: 13px; margin: 4px 0; word-break: break-all; }
  .actions { margin-top: 28px; display: flex; gap: 12px; justify-content: center; }
  button { border-radius: 8px; padding: 10px 20px; font-size: 14px; font-weight: 600;
           cursor: pointer; display: inline-block; }
  .approve { background: #4f7cff; color: white; border: none; }
  .deny { background: transparent; color: #9a9fa8; border: 1px solid #2a2e37; }
  .warning { margin-top: 20px; font-size: 12px; color: #6b7280; }
"""


def render_consent_page(
    *,
    client_name: str,
    client_uri: str | None,
    ip_address: str | None,
    registered_at: int | None,
    nonce: str,
    approve_action: str,
    deny_action: str,
) -> str:
    safe_name = html.escape(client_name or "Unknown application")
    safe_uri = html.escape(client_uri) if client_uri else None
    safe_nonce = html.escape(nonce)
    safe_approve_action = html.escape(approve_action, quote=True)
    safe_deny_action = html.escape(deny_action, quote=True)

    meta_lines = []
    if safe_uri:
        meta_lines.append(f'<p class="meta">{safe_uri}</p>')
    if registered_at:
        registered_dt = datetime.fromtimestamp(registered_at, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        line = f'<p class="meta">Registered {html.escape(registered_dt)}'
        if ip_address:
            line += f" from <code>{html.escape(ip_address)}</code>"
        line += "</p>"
        meta_lines.append(line)
    meta_html = "\n    ".join(meta_lines)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Authorize {safe_name} - Jarvis Registry</title>
<style>{_STYLE}</style>
</head>
<body>
  <div class="card">
    <h1><span class="app-name">{safe_name}</span> wants to access your Jarvis Registry account</h1>
    {meta_html}
    <p class="meta">This will let it obtain an access token to act on your behalf via the MCP gateway.</p>
    <div class="actions">
      <form method="POST" action="{safe_approve_action}" style="display:inline;">
        <input type="hidden" name="nonce" value="{safe_nonce}" />
        <button type="submit" class="approve">Authorize</button>
      </form>
      <form method="POST" action="{safe_deny_action}" style="display:inline;">
        <input type="hidden" name="nonce" value="{safe_nonce}" />
        <button type="submit" class="deny">Cancel</button>
      </form>
    </div>
    <p class="warning">Only authorize applications you recognize and trust. This request
    originated from a third-party OAuth client, not Jarvis Registry itself.</p>
  </div>
</body>
</html>"""


def render_consent_error_page() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Link expired - Jarvis Registry</title>
<style>{_STYLE}</style>
</head>
<body>
  <div class="card">
    <h1>This link has expired</h1>
    <p class="meta">Please return to your MCP client and retry the connection.</p>
  </div>
</body>
</html>"""


def render_device_code_entry_page(*, verify_action: str, error: str | None = None) -> str:
    safe_action = html.escape(verify_action, quote=True)
    error_html = f'<p class="meta" style="color:#f87171;">{html.escape(error)}</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Enter device code - Jarvis Registry</title>
<style>{_STYLE}
  input[type="text"] {{ width: 100%; box-sizing: border-box; margin-top: 16px; padding: 12px;
    font-size: 18px; letter-spacing: 2px; text-align: center; text-transform: uppercase;
    border-radius: 8px; border: 1px solid #2a2e37; background: #12141a; color: #e6e6e6; }}
</style>
</head>
<body>
  <div class="card">
    <h1>Enter your device code</h1>
    <p class="meta">Enter the code shown on your device to continue.</p>
    {error_html}
    <form method="POST" action="{safe_action}">
      <input type="text" name="user_code" placeholder="WDJB-MJHT" maxlength="16" required autofocus />
      <div class="actions">
        <button type="submit" class="approve">Continue</button>
      </div>
    </form>
  </div>
</body>
</html>"""


def render_device_code_confirm_page(*, user_code: str, verify_action: str) -> str:
    safe_code = html.escape(user_code)
    safe_action = html.escape(verify_action, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Confirm device code - Jarvis Registry</title>
<style>{_STYLE}
  .code {{ font-size: 28px; font-weight: 700; letter-spacing: 4px; color: #7c9eff; margin: 16px 0; }}
</style>
</head>
<body>
  <div class="card">
    <h1>Does this match your device?</h1>
    <p class="code">{safe_code}</p>
    <p class="meta">Confirm this code is the same one shown on the device you're setting up,
    then sign in to continue. Only continue if you started this request yourself.</p>
    <form method="POST" action="{safe_action}">
      <input type="hidden" name="user_code" value="{safe_code}" />
      <div class="actions">
        <button type="submit" class="approve">Continue to sign in</button>
      </div>
    </form>
  </div>
</body>
</html>"""


def render_device_approved_page() -> str:
    return (
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Device connected - Jarvis Registry</title>
<style>"""
        + _STYLE
        + """</style>
</head>
<body>
  <div class="card">
    <h1>Your device is connected</h1>
    <p class="meta">You can close this window and return to your device.</p>
  </div>
</body>
</html>"""
    )


def render_device_denied_page() -> str:
    return (
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Access denied - Jarvis Registry</title>
<style>"""
        + _STYLE
        + """</style>
</head>
<body>
  <div class="card">
    <h1>You denied this request</h1>
    <p class="meta">Your device will not be authorized. You can close this window.</p>
  </div>
</body>
</html>"""
    )


def render_device_link_error_page() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Invalid code - Jarvis Registry</title>
<style>{_STYLE}</style>
</head>
<body>
  <div class="card">
    <h1>This code is invalid or has expired</h1>
    <p class="meta">Return to your device and check the code, or start a new request.</p>
  </div>
</body>
</html>"""
