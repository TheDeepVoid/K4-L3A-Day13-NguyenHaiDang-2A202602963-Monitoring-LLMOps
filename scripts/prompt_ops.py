"""Manage the lab prompt in Langfuse: create versions, move labels, roll back.

The lab expects promote/rollback of the `production` label to be provable.
Doing it in the UI is fine, but a script makes the sequence reproducible and
reviewable, and its output can be captured as evidence.

Usage (credentials come from .env):
    python scripts/prompt_ops.py create-v1
    python scripts/prompt_ops.py create-v2
    python scripts/prompt_ops.py promote 2          # production -> v2
    python scripts/prompt_ops.py rollback           # production -> v1
    python scripts/prompt_ops.py show               # versions + labels + trace usage
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import the app's dotenv handling by loading .env before the Langfuse SDK is
# imported, so the client is never constructed with missing credentials.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from app.cli import configure_utf8_stdio  # noqa: E402

PROMPT_NAME = os.getenv("LANGFUSE_PROMPT_NAME", "day13-chat")

# v1 - baseline. Keeps the three variables app/prompt_management.py compiles:
# {{feature}}, {{docs}}, {{message}}.
PROMPT_V1 = """You are the Day 13 lab assistant answering questions for the {{feature}} feature.

<context>
{{docs}}
</context>

<question>
{{message}}
</question>

Answer the question using only the context above. If the context does not
contain the answer, say so plainly instead of guessing."""

# v2 - candidate. One deliberate edit: an explicit length limit, so the
# generated answer stays readable in a trace. Everything else is identical to
# v1 so a diff shows exactly one cause.
PROMPT_V2 = """You are the Day 13 lab assistant answering questions for the {{feature}} feature.

<context>
{{docs}}
</context>

<question>
{{message}}
</question>

Answer the question using only the context above. If the context does not
contain the answer, say so plainly instead of guessing.

Keep the answer under 80 words."""

BASE_LABELS = ["baseline"]
ROLLBACK_LABELS = ["baseline", "production"]


def _client():
    from langfuse import get_client

    return get_client()


def create(client, text: str, labels: list[str], commit_message: str) -> int:
    prompt = client.create_prompt(
        name=PROMPT_NAME,
        type="text",
        prompt=text,
        labels=labels,
        commit_message=commit_message,
    )
    print(f"created {PROMPT_NAME} version {prompt.version} with labels {labels}")
    return prompt.version


def set_labels(client, version: int, labels: list[str]) -> None:
    client.update_prompt(name=PROMPT_NAME, version=version, new_labels=labels)
    print(f"{PROMPT_NAME} version {version} labels -> {labels}")


def show(client) -> None:
    """Print every version with its labels, resolved through the SDK."""
    print(f"prompt: {PROMPT_NAME}")
    for label in ("production", "baseline", "candidate", "latest"):
        try:
            prompt = client.get_prompt(PROMPT_NAME, label=label, type="text")
        except Exception as exc:  # label not assigned -> 404
            print(f"  label {label:<10} -> not assigned ({type(exc).__name__})")
            continue
        print(f"  label {label:<10} -> version {prompt.version}")
    for version in (1, 2, 3):
        try:
            prompt = client.get_prompt(PROMPT_NAME, version=version, type="text")
        except Exception:
            continue
        print(f"  version {version}: {prompt.prompt.splitlines()[0][:60]}...")


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create-v1", "create-v2", "promote", "rollback", "show"])
    parser.add_argument("version", nargs="?", type=int, help="version for promote")
    args = parser.parse_args()

    client = _client()

    if args.action == "create-v1":
        create(client, PROMPT_V1, ["baseline", "production"], "baseline prompt for CP2")
    elif args.action == "create-v2":
        create(client, PROMPT_V2, ["candidate"], "candidate: explicit 80-word answer limit")
    elif args.action == "promote":
        if args.version is None:
            parser.error("promote needs a version, e.g. promote 2")
        set_labels(client, args.version, ["production"])
    elif args.action == "rollback":
        set_labels(client, 1, ROLLBACK_LABELS)
    elif args.action == "show":
        show(client)

    client.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
