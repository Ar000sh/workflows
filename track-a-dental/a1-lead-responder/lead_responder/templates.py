"""Fixed acknowledgment texts. No AI involved: reviewed once, can never hallucinate."""
from __future__ import annotations

from lead_responder.practice import Practice
from lead_responder.router import COMBINED_LANGUAGE

_TEXTS = {
    "de": {
        "greeting": "Hallo {name},",
        "greeting_no_name": "Hallo,",
        "received": ("vielen Dank für Ihre Nachricht an die {practice_name}. Wir haben Ihre Anfrage erhalten. "
                     "Unser Praxisteam meldet sich persönlich bei Ihnen, in der Regel noch am selben Praxistag."),
        "emergency_open": "Bei akuten Schmerzen rufen Sie uns bitte jetzt direkt an: {phone}.",
        "emergency_closed": ("Unsere Praxis ist gerade geschlossen. Bei akuten Zahnschmerzen erreichen Sie "
                             "den zahnärztlichen Notdienst unter {notdienst_phone}."),
        "emergency_112": ("Bei lebensbedrohlichen Beschwerden, zum Beispiel starken Blutungen oder einer "
                          "Schwellung im Gesicht mit Atemnot, wählen Sie bitte sofort die 112."),
        "signoff": "Mit freundlichen Grüßen\nIhr Praxisteam der {practice_name}",
    },
    "en": {
        "greeting": "Hello {name},",
        "greeting_no_name": "Hello,",
        "received": ("thank you for your message to {practice_name}. We have received your inquiry and our "
                     "team will get back to you personally, usually on the same working day."),
        "emergency_open": "If you are in acute pain, please call us right away: {phone}.",
        "emergency_closed": ("Our practice is currently closed. For acute toothache, please contact the dental "
                             "emergency service at {notdienst_phone}."),
        "emergency_112": ("For life-threatening symptoms, such as heavy bleeding or facial swelling with "
                          "difficulty breathing, call 112 immediately."),
        "signoff": "Kind regards\nThe team at {practice_name}",
    },
}


def acknowledgment(
    language: str,
    *,
    name: str,
    practice: Practice,
    emergency: bool,
    practice_open: bool,
    safe_info: str | None = None,
) -> str:
    if language == COMBINED_LANGUAGE:
        # Used when we can't trust the language or the AI: no AI paragraph here.
        german = _single("de", name, practice, emergency, practice_open, None)
        english = _single("en", name, practice, emergency, practice_open, None)
        return f"{german}\n\n---\n\n{english}"
    if language not in _TEXTS:
        raise ValueError(f"no acknowledgment template for language {language!r}")
    return _single(language, name, practice, emergency, practice_open, safe_info)


def _single(language, name, practice, emergency, practice_open, safe_info) -> str:
    texts = _TEXTS[language]
    values = {
        "practice_name": practice.name,
        "phone": practice.phone,
        "notdienst_phone": practice.notdienst_phone,
    }
    name = name.strip()
    paragraphs = [
        texts["greeting"].format(name=name) if name else texts["greeting_no_name"],
        texts["received"].format(**values),
    ]
    if emergency:
        paragraphs.append(texts["emergency_open" if practice_open else "emergency_closed"].format(**values))
        paragraphs.append(texts["emergency_112"])
    if safe_info:
        paragraphs.append(safe_info)
    paragraphs.append(texts["signoff"].format(**values))
    return "\n\n".join(paragraphs)
