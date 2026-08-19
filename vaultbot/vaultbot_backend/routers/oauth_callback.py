"""Google OAuth 2.0 callback router.

Receives the authorization code from Google's redirect after the user
grants consent, exchanges it for access/refresh tokens via the
google_workspace custom tool, and returns a simple HTML success page.

The /callback path is auth-exempt (see auth.py) because Google's redirect
arrives without the VaultBot shared-secret token.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["oauth"])


@router.get("/callback")
async def google_oauth_callback(request: Request):
    """Google OAuth 2.0 callback — exchanges auth code for tokens."""
    code = request.query_params.get("code", "")
    error = request.query_params.get("error", "")

    if error:
        return HTMLResponse(
            content=f"<h2>Authorization failed</h2><p>Google returned error: {error}</p>",
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            content="<h2>Missing authorization code</h2><p>No 'code' parameter in the callback URL.</p>",
            status_code=400,
        )

    # Exchange the auth code for tokens using the google_workspace tool.
    import os
    import sys

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    try:
        from custom_tools.google_workspace import run as gw_run

        result = gw_run({"action": "callback", "code": code})
        if "error" in result:
            return HTMLResponse(
                content=f"<h2>Token exchange failed</h2><p>{result['error']}</p>",
                status_code=500,
            )

        return HTMLResponse(
            content=(
                "<h2>Authorization successful!</h2>"
                "<p>Tokens have been stored. You can close this tab "
                "and return to Obsidian.</p>"
            )
        )
    except Exception as e:
        return HTMLResponse(
            content=f"<h2>Internal error</h2><p>{e}</p>",
            status_code=500,
        )
