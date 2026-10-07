"""Verify the configured AI provider actually works, without starting the app.

Run from the backend folder with the project's virtualenv:

    venv\\Scripts\\python.exe check_ai.py        (Windows)
    venv/bin/python check_ai.py                 (macOS / Linux)

Checks, in order: the SDK is installed, a key is present in .env, the key is accepted,
the configured model exists, and a real prompt returns real text.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import ai as ailib  # noqa: E402  (must follow load_dotenv)


def line(ok, label, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    return ok


async def main():
    print("\nAptimizer AI configuration check\n" + "=" * 34)

    p = ailib.provider()
    if not line(p["configured"], "A provider key is configured", p.get("detail", "")):
        print("\nFix backend/.env, then run this again.\n")
        return 1
    print(f"         provider={p['provider']}  model={p['model']}")
    if p.get("warning"):
        line(False, "The key looks like it belongs to this provider", p["warning"])

    if p["provider"] == "gemini":
        try:
            from google import genai
        except ImportError:
            line(False, "google-genai is installed", "run: pip install google-genai")
            return 1
        line(True, "google-genai is installed")
        key = os.environ["GEMINI_API_KEY"].strip()
        print(f"         key starts {key[:6]}... ends ...{key[-4:]} (length {len(key)})")
        try:
            names = [m.name.split("/")[-1] for m in genai.Client(api_key=key).models.list()]
        except Exception as exc:
            line(False, "The API key is accepted", str(exc)[:160])
            return 1
        line(True, "The API key is accepted", f"{len(names)} models visible")
        if not line(p["model"] in names, f"Model '{p['model']}' is available to this key"):
            print(f"         try: {', '.join(n for n in names if 'flash' in n)[:120]}")
            print("         then set GEMINI_MODEL in backend/.env")
            return 1

    elif p["provider"] in ("grok", "groq"):
        try:
            from openai import OpenAI
        except ImportError:
            line(False, "the openai package is installed", "run: pip install openai")
            return 1
        line(True, "the openai package is installed")
        key = os.environ[p["key_env"]].strip()
        print(f"         key starts {key[:6]}... ends ...{key[-4:]} (length {len(key)})")
        base = ailib.XAI_BASE_URL if p["provider"] == "grok" else ailib.GROQ_BASE_URL
        client = OpenAI(api_key=key, base_url=base)
        print(f"         endpoint {base}")
        try:
            names = [m.id for m in client.models.list()]
        except Exception as exc:
            line(False, f"The API key is accepted by {p['provider']}", str(exc)[:180])
            console = ("https://console.x.ai/team/default/api-keys" if p["provider"] == "grok"
                       else "https://console.groq.com/keys")
            print(f"\n  A 401 means the key is wrong or revoked -- copy it again from {console}\n")
            return 1
        line(True, f"The API key is accepted by {p['provider']}", f"{len(names)} models visible")
        if not line(p["model"] in names, f"Model '{p['model']}' is available to this key"):
            print(f"         available: {', '.join(names[:8])}")
            print(f"         set {p['provider'].upper()}_MODEL in backend/.env to one of these")
            return 1

    try:
        result = await ailib.generate_markdown(
            "You are a civil engineer. Answer in one short sentence.",
            "A plot is 2000 m2 with an FAR of 2.0. State the permitted built-up area.",
        )
    except Exception as exc:
        line(False, "A live prompt returns text", str(exc)[:200])
        return 1
    line(True, "A live prompt returns text", f"via {result['provider']}/{result['model']}")
    print(f"\n  Model said: {result['text'].strip()[:200]}")
    print("\nAll checks passed. Restart the backend and the AI buttons will work.\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
