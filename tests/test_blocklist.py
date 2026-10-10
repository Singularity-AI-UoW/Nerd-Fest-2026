from pathlib import Path

import pytest

from kiosk.blocklist import Blocklist, normalise

REAL_FILE = Path(__file__).resolve().parent.parent / "kiosk" / "blocklist.txt"

TRICKY = ["p00p", "POOOOP", "p o o p", "k1ll", "a b-o-m-b"]
SAFE = ["a dragon riding a bike", "a skilled kiwi", "a scunthorpe sheep", "a pooped out puppy"]
HALLOWEEN_SAFE = ["a spooky zombie pumpkin", "a dragon fight", "a monster truck"]


@pytest.fixture
def bl(tmp_path):
    f = tmp_path / "bl.txt"
    f.write_text("poop\nkill\nbomb\n", encoding="utf-8")
    return Blocklist.from_file(f)


def test_tricky_blocked(bl):
    assert all(bl.hits(t) for t in TRICKY)


def test_safe_not_blocked(bl):
    assert not any(bl.hits(t) for t in SAFE)


def test_normalise_substitutions_and_squash():
    assert normalise("P00P") == "poop"
    assert normalise("k1ll $h@t") == "kill shat"
    assert normalise("b-o-m-b!") == "bomb"
    assert normalise("a b-o-m-b") == "a bomb"
    assert normalise("heeeello") == "hello"


def test_spaced_letters_inside_a_sentence(bl):
    assert bl.hits("a cat and a b o m b") == ["bomb"]
    assert bl.hits("p o o p") == ["poop"]


def test_never_substring_inside_longer_words(bl):
    assert not bl.hits("bombastic skilled killjoy pooping")


def test_hits_returns_matched_entries_once(bl):
    assert bl.hits("kill kill k1ll bomb") == ["kill", "bomb"]


def test_multi_word_entries_match_consecutive_whole_words(tmp_path):
    f = tmp_path / "bl.txt"
    f.write_text("# comment\n\nhang myself  # trailing comment\n", encoding="utf-8")
    bl = Blocklist.from_file(f)
    assert bl.hits("please hang myself") == ["hang myself"]
    assert not bl.hits("hang my self-portrait")
    assert not bl.hits("myself hang")


def test_real_file_size_and_blocks_obvious_terms():
    bl = Blocklist.from_file(REAL_FILE)
    assert 120 <= len(bl) <= 200
    for text in ["a b0mb", "k i l l", "naked person", "p o r n"]:
        assert bl.hits(text), text


@pytest.mark.parametrize("text", HALLOWEEN_SAFE + SAFE + ["a killer whale", "a kiwi with a toy gun"])
def test_real_file_does_not_block_kid_prompts(text):
    assert not Blocklist.from_file(REAL_FILE).hits(text)
