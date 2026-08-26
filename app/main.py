"""Entry point: run the Manufacturing Investigator agent from the command line."""

from __future__ import annotations

import asyncio
import sys

from app.agents.production import AGENT_NAME, investigate
from app.config import require_api_key

DEFAULT_QUESTION = (
    "Explain what information would be needed to investigate a production "
    "drop on Line 4."
)


def main() -> None:
    require_api_key()
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION

    print(f"[{AGENT_NAME}]")
    print(f"Q: {question}\n")

    answer = asyncio.run(investigate(question))
    print(f"A: {answer}")


if __name__ == "__main__":
    main()
