from lead_responder.limits import clean_text, mailbox_key


def test_mailbox_key_strips_plus_tag_and_case():
    assert mailbox_key("Victim+1@Example.com") == "victim@example.com"
    assert mailbox_key("victim+2@example.com") == "victim@example.com"


def test_mailbox_key_removes_dots_only_for_gmail():
    assert mailbox_key("v.i.c@gmail.com") == "vic@gmail.com"
    assert mailbox_key("v.i.c@example.com") == "v.i.c@example.com"
    assert mailbox_key("v.i.c+x@googlemail.com") == "vic@gmail.com"


def test_clean_text_removes_nul():
    assert clean_text("a\x00b") == "ab"
