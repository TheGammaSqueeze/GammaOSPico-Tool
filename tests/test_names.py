import unicodedata

from pico_tool.card.names import (LegacyForms, ascii_fold, base_ext, clean_title, console_name, cut_u8,
                                  ends_with_any, fat_safe, fold, fold_ascii, fold_cp, legacy_key, letter,
                                  named_forms, sort_key)


def test_console_name_is_the_real_name():
    assert console_name("Pokemon.gba") == "Pokemon.gba"
    assert console_name("Pokémon.gba") == "Pokémon.gba"
    assert console_name("ポケモン.gba") == "ポケモン.gba"
    assert console_name("A\U0001F600B") == "A\U0001F600B"
    # a decomposed name stays decomposed
    nfd = unicodedata.normalize("NFD", "Pokémon.gba")
    assert console_name(nfd) == nfd and console_name(nfd) != "Pokémon.gba"
    # a surrogate without its other half reads as U+FFFD
    assert console_name("a\udcff.gba") == "a\ufffd.gba"


def test_console_name_longer_than_255_bytes_keeps_its_extension():
    assert console_name("x" * 300) == "x" * 255
    assert console_name("x" * 300 + ".gba") == "x" * 251 + ".gba"
    long = "ポ" * 100 + ".gba"                    # 304 bytes in UTF-8, 104 UTF-16 units
    got = console_name(long)
    assert got == "ポ" * 83 + ".gba" and len(got.encode()) == 253
    # no dot among the last 16 units: just cut at a whole character
    assert console_name("ポ" * 100) == "ポ" * 85
    # an emoji (four bytes) is never cut in half
    got = console_name("\U0001F600" * 70 + ".gba")
    assert got == "\U0001F600" * 62 + ".gba"


def test_cut_u8_keeps_whole_characters():
    assert cut_u8("é" * 100, 127) == "é" * 63
    assert cut_u8("abc", 127) == "abc"
    assert len(cut_u8("ポ" * 50, 127).encode()) == 126


def test_ascii_fold_is_for_file_names():
    assert ascii_fold("ABCÉ") == "abcÉ"


def test_fold_cp_follows_the_console():
    assert chr(fold_cp(ord("é"))) == "e" and chr(fold_cp(ord("Ł"))) == "L" and chr(fold_cp(ord("ß"))) == "s"
    assert fold_cp(0x0301) == 0                     # a combining accent is dropped
    assert chr(fold_cp(ord("Ｚ"))) == "Z"           # fullwidth ASCII
    assert fold_cp(ord("×")) == ord("×")            # not a letter
    assert chr(fold_cp(ord("Ệ"))) == "E" and chr(fold_cp(ord("ệ"))) == "e"
    assert fold_cp(ord("ж")) == ord("ж") and fold_cp(ord("ポ")) == ord("ポ")
    assert fold_ascii("Pokémon ポ") == "Pokemon _"


def test_sort_folds_accents_and_puts_other_scripts_after_ascii():
    names = ["Zelda", "Ébène", "ポケモン", "apple", "Ωmega", "Émile", "eagle", "Ångström", "1942", "Ｆull"]
    got = sorted(names, key=sort_key)
    assert got == ["1942", "Ångström", "apple", "eagle", "Ébène", "Émile", "Ｆull", "Zelda", "Ωmega", "ポケモン"]
    # decomposed and composed sort alike
    assert sort_key(unicodedata.normalize("NFD", "Émile")) == sort_key("Émile") == sort_key("emile")


def test_fold_for_search():
    assert fold("Pokémon") == "pokemon"
    assert fold(unicodedata.normalize("NFD", "Pokémon")) == "pokemon"
    # a decomposed letter outside Latin is composed first, the way the menu draws it
    assert fold(unicodedata.normalize("NFD", "й")) == fold("й") == "й"
    # Hangul written as jamo is one syllable
    assert fold(unicodedata.normalize("NFD", "한국")) == "한국"
    assert fold("ＭＡＲＩＯ") == "mario"


def test_letter_folds_accents():
    assert letter("zelda") == "Z" and letter("Élan") == "E" and letter("ñandu") == "N"
    assert letter("1942") == "#" and letter("...") == "#"
    assert letter("Ωmega") == "#" and letter("ポケモン") == "#" and letter("한국") == "#"
    assert letter("Привет") == "#" and letter("عربي") == "#"
    assert letter(unicodedata.normalize("NFD", "Élan")) == "E"
    assert letter("★ Star") == "S"                  # a symbol is passed over
    assert letter("Ｚero") == "Z"


def test_legacy_key_is_the_old_underscore_form():
    assert legacy_key("Pokémon") == ("Pok_mon", True)
    assert legacy_key(unicodedata.normalize("NFD", "Pokémon")) == ("Poke_mon", True)
    assert legacy_key("ポケモン") == ("____", True)
    assert legacy_key("A\U0001F600B") == ("A__B", True)      # two UTF-16 units
    assert legacy_key("Plain") == ("Plain", False)
    assert legacy_key("é" * 200) == ("_" * 127, True)


def test_legacy_forms_shared_and_own():
    forms = LegacyForms(["ポケモン", "テトリス", "Pokémon", "Zelda", "Café", "caf_"])
    assert forms.own("ポケモン") is None                      # "____" is two games' form
    assert forms.own("Pokémon") == "Pok_mon"
    assert forms.own("Zelda") is None                         # all ASCII: no older form
    assert forms.own("Café") is None                          # another game is named "caf_"


def test_named_forms_order():
    forms = LegacyForms(["Pokémon (France)", "Mario vs. Donkey Kong"])
    assert named_forms("Pokémon (France)", "ART", forms) == ["Pokémon (France).ART", "Pok_mon (France).ART"]
    assert named_forms("Pokémon (France)", "ART", None) == ["Pokémon (France).ART"]
    assert named_forms("Mario vs. Donkey Kong", "ART", forms) == ["Mario vs. Donkey Kong.ART", "Mario vs.ART"]


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
    assert clean_title("Pokémon Émeraude (France)") == "Pokémon Émeraude"
    assert clean_title("ポケモン (Japan) [!]") == "ポケモン"


def test_base_ext_cuts_only_rom_extensions():
    assert base_ext("Mario vs. Donkey Kong", "ART") == "Mario vs. Donkey Kong.ART"
    assert base_ext("Game.gba", "ART") == "Game.ART"
    assert base_ext("Game.GBC", "sav") == "Game.sav"
    assert base_ext("Mario vs. Donkey Kong", "ART", legacy=True) == "Mario vs.ART"
    assert base_ext("Café.gba", "ART") == "Café.ART"


def test_ends_with_any_needs_a_longer_name():
    assert ends_with_any("a.gba", (".gba",)) == 4
    assert ends_with_any(".gba", (".gba",)) == 0
    assert ends_with_any("A.SFC", (".sfc", ".smc")) == 4
    assert ends_with_any("é.GBA", (".gba",)) == 4


def test_fat_safe():
    assert fat_safe("Game (USA).ART") and not fat_safe("a:b.ART") and not fat_safe("x?.ART")
    assert fat_safe("ポケモン.ART")
    assert not fat_safe("Bad\udcff.ART")                     # bytes that are not UTF-8
