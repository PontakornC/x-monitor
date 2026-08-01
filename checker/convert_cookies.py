"""Convert a Cookie-Editor JSON export into Playwright's storage_state.json format.

Usage:
    python convert_cookies.py path/to/cookie-editor-export.json
"""

import json
import sys

SAMESITE_MAP = {
    "no_restriction": "None",
    "lax": "Lax",
    "strict": "Strict",
    None: "Lax",
}


def convert(raw_cookies: list[dict]) -> dict:
    cookies = []
    for c in raw_cookies:
        expires = -1 if c.get("session") else c["expirationDate"]
        cookies.append({
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c["path"],
            "expires": expires,
            "httpOnly": c["httpOnly"],
            "secure": c["secure"],
            "sameSite": SAMESITE_MAP.get(c.get("sameSite"), "Lax"),
        })
    return {"cookies": cookies, "origins": []}


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python convert_cookies.py <cookie-editor-export.json>")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        raw = json.load(f)

    state = convert(raw)

    out_path = "x-state.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"บันทึกแล้ว: {out_path} ({len(state['cookies'])} cookies)")


if __name__ == "__main__":
    main()
