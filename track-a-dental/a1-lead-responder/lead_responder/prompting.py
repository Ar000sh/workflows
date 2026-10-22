"""How untrusted patient text is put into a prompt."""


def inquiry_block(text: str) -> str:
    # Angle brackets are escaped so a message cannot close the <inquiry> tag and put its own
    # instructions outside it. Everything inside the tag is data, never instructions.
    safe = text.replace("<", "&lt;").replace(">", "&gt;")
    return f"<inquiry>\n{safe}\n</inquiry>"
