"""The text rules that shape every dictation.

Pure functions, no OS, no model — so this runs anywhere, including Windows CI.
"""

import unittest

from voicetype.text import apply_snippets, apply_vocab, clean_text


class Language(unittest.TestCase):
    def test_swedish_i_is_a_word(self):
        # "i" means "in". Capitalising it is an English rule that has no business
        # firing here, and it corrupted every Swedish sentence containing it.
        self.assertEqual(clean_text("jag är i skolan", lang="sv")[0],
                         "Jag är i skolan")

    def test_danish_and_norwegian_too(self):
        for lang in ("da", "no", "nb"):
            self.assertEqual(clean_text("vi er i byen", lang=lang)[0], "Vi er i byen")

    def test_english_i_still_capitalises(self):
        self.assertEqual(clean_text("i think i can", lang="en")[0], "I think I can")


class Vocab(unittest.TestCase):
    def test_fixes_casing(self):
        self.assertEqual(apply_vocab("i use openai", ["OpenAI"]), "i use OpenAI")

    def test_multiword(self):
        self.assertEqual(apply_vocab("go to new york", ["New York"]), "go to New York")

    def test_explicit_correction(self):
        self.assertEqual(apply_vocab("ask clawd", [], {"clawd": "Claude"}), "ask Claude")

    def test_never_rewrites_a_real_word(self):
        # The whole reason there is no fuzzy matching: "cloud" and "clawd" sit at
        # the same edit distance from "Claude", so guessing would eat this sentence.
        self.assertEqual(apply_vocab("the cloud is clear", ["Claude"],
                                     {"clawd": "Claude"}), "the cloud is clear")

    def test_partial_words_untouched(self):
        self.assertEqual(apply_vocab("openaisomething", ["OpenAI"]), "openaisomething")

    def test_regex_chars_in_a_term_are_literal(self):
        self.assertEqual(apply_vocab("mail c++ dev", ["C++"]), "mail C++ dev")


class Snippets(unittest.TestCase):
    def test_expands(self):
        s = [{"trigger": "my calendar", "text": "https://cal.com/x"}]
        self.assertEqual(apply_snippets("book my calendar please", s),
                         "book https://cal.com/x please")

    def test_longest_trigger_wins(self):
        s = [{"trigger": "my email", "text": "a@b.c"},
             {"trigger": "my work email", "text": "work@b.c"}]
        self.assertEqual(apply_snippets("send my work email", s), "send work@b.c")


class Cleanup(unittest.TestCase):
    def test_fillers(self):
        self.assertEqual(clean_text("um so uh yeah", lang="en")[0], "So yeah")

    def test_paragraph_survives_space_collapsing(self):
        t, _ = clean_text("one new paragraph two", lang="en")
        self.assertIn("\n\n", t)

    def test_press_enter(self):
        t, enter = clean_text("ship it press enter", lang="en")
        self.assertTrue(enter)
        self.assertEqual(t, "Ship it")

    def test_curly_apostrophe(self):
        self.assertEqual(clean_text("it’s done", lang="en")[0], "It's done")

    def test_empty(self):
        self.assertEqual(clean_text("", lang="en"), ("", False))
        self.assertEqual(clean_text(None, lang="en"), ("", False))

    def test_capitalises_sentences(self):
        self.assertEqual(clean_text("hello. there is more. ok", lang="en")[0],
                         "Hello. There is more. Ok")


class Order(unittest.TestCase):
    def test_vocab_runs_before_capitalisation(self):
        t, _ = clean_text("openai is good", lang="en", vocab=["OpenAI"])
        self.assertEqual(t, "OpenAI is good")

    def test_snippet_text_is_not_filler_stripped_into_nonsense(self):
        s = [{"trigger": "sig", "text": "Amir"}]
        t, _ = clean_text("sig", lang="en", snippets=s)
        self.assertEqual(t, "Amir")


if __name__ == "__main__":
    unittest.main()
