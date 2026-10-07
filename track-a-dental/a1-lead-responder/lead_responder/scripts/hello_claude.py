"""Layer 1: the smallest possible Claude call — no wrapper, no retries.

Run:  uv run python -m lead_responder.scripts.hello_claude
"""
import sys

import anthropic
from dotenv import load_dotenv

from shared.llm import cost_usd

MODEL = "claude-haiku-4-5"
SYSTEM = "Du bist die freundliche Rezeption einer Zahnarztpraxis in Hannover. Antworte kurz und höflich."
INQUIRY = "Hallo, ich bin neu in Hannover und suche eine Zahnarztpraxis. Nehmen Sie noch neue Patienten auf?"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=SYSTEM,
        messages=[{"role": "user", "content": INQUIRY}],
    )
    # response.content is a list of blocks; we only want the text ones.
    print("".join(block.text for block in response.content if block.type == "text"))
    usage = response.usage
    cost = cost_usd(response.model, usage.input_tokens, usage.output_tokens)
    print(f"\n--- {response.model} | input {usage.input_tokens} tokens | output {usage.output_tokens} tokens | ${cost:.5f}")


if __name__ == "__main__":
    main()
