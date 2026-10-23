"""Local frontend companion: seed demo data and configure this process, then serve.

Run from the repository root with backend/.venv/Scripts/python.exe -m backend.tools.dev.
The credential is printed only for the developer to paste into the local frontend.
"""
import argparse
import json
import os
import secrets

import uvicorn

from .demo import seed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    user = seed()
    token = secrets.token_urlsafe(32)
    os.environ["LUCID_DATABASE_URL"] = "sqlite:///backend/demo.db"
    os.environ["LUCID_TOKENS_JSON"] = json.dumps({token: user})
    print("\nLocal demo token (private; paste in frontend):\n" + token, flush=True)
    print("\nFrontend: http://localhost:5173 | Backend docs: http://127.0.0.1:"
          + str(args.port) + "/docs\nOne worker; stop with Ctrl+C.\n", flush=True)
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=args.port, ws_max_size=16384)


if __name__ == "__main__":
    main()
