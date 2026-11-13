from pico_tool.card.names import base_ext, clean_title, console_name, ends_with_any, fat_safe, fold, letter


def test_console_name_keeps_ascii_and_turns_the_rest_into_underscores():
    assert console_name("Pokemon.gba") == "Pokemon.gba"
    assert console_name("Pokémon.gba") == "Pok_mon.gba"
    # one "_" per UTF-16 unit: an emoji outside the BMP is two
    assert console_name("A\U0001F600B") == "A__B"
    assert len(console_name("x" * 300)) == 255


def test_fold_is_ascii_only():
    assert fold("ABCÉ") == "abcÉ"


def test_clean_title_drops_trailing_tags():
    assert clean_title("Advance Wars (USA).gba") == "Advance Wars"
    assert clean_title("Pokemon - Emerald (USA, Europe) [!]") == "Pokemon - Emerald"
    assert clean_title("Game [x] (USA)") == "Game"
    assert clean_title("Foo (a(b)c)") == "Foo"
    assert clean_title("Mario vs. Donkey Kong (USA)") == "Mario vs. Donkey Kong"
    # a name that is all tag keeps itself (the console's rule, trailing space and all)
    assert clean_title("(USA) ") == "(USA) "
    assert clean_title("(A) (B)") == "(A) "
    assert clean_title("No Tags") == "No Tags"


def test_base_ext_cuts_only_rom_extensions():
    assert base_ext("Mario vs. Donkey Kong", "ART") == "Mario vs. Donkey Kong.ART"
    assert base_ext("Game.gba", "ART") == "Game.ART"
    assert base_ext("Game.GBC", "sav") == "Game.sav"
    assert base_ext("Mario vs. Donkey Kong", "ART", legacy=True) == "Mario vs.ART"


def test_ends_with_any_needs_a_longer_name():
    assert ends_with_any("a.gba", (".gba",)) == 4
    assert ends_with_any(".gba", (".gba",)) == 0
    assert ends_with_any("A.SFC", (".sfc", ".smc")) == 4


def test_letter_and_fat_safe():
    assert letter("zelda") == "Z" and letter("1942") == "#" and letter("...") == "#"
    assert fat_safe("Game (USA).ART") and not fat_safe("a:b.ART") and not fat_safe("x?.ART")
