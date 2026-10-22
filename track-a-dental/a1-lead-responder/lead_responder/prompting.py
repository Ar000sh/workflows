"""How untrusted patient text is handled: put into a prompt, or into a reply."""
import re
import unicodedata

MAX_NAME_CHARS = 60
_LINK_OR_EMAIL = re.compile(r"https?:|www\.|://|@", re.IGNORECASE)
_MARKUP = re.compile(r"[<>{}\[\]|]")


def inquiry_block(text: str) -> str:
    # Angle brackets are escaped so a message cannot close the <inquiry> tag and put its own
    # instructions outside it. Everything inside the tag is data, never instructions.
    safe = text.replace("<", "&lt;").replace(">", "&gt;")
    return f"<inquiry>\n{safe}\n</inquiry>"


def sanitize_name(name) -> str:
    # The name comes from a form field and is inserted into replies that nobody reviews (auto-reply,
    # acknowledgment). So it may only ever be a short, plain name: anything else becomes "" and the
    # greeting is just "Hallo,". Hostile input never raises.
    if not isinstance(name, str):
        return ""
    name = unicodedata.normalize("NFKC", name)
    # Control and invisible characters (newlines, tabs, zero-width spaces) become spaces.
    name = "".join(" " if unicodedata.category(char).startswith("C") else char for char in name)
    if _LINK_OR_EMAIL.search(name) or _MARKUP.search(name):
        return ""
    # Keep letters, marks, spaces, hyphens, apostrophes and dots; drop digits and other punctuation.
    name = "".join(char for char in name if char.isalpha() or unicodedata.category(char).startswith("M")
                   or char in " -'.")
    return " ".join(name.split())[:MAX_NAME_CHARS].strip()
