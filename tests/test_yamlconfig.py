"""Day 12: the strict YAML-subset reader and the shipped profile/ranking files."""
import copy

import pytest

from discovery import profile as prof
from discovery import yamlconfig as y


def test_reads_the_supported_subset():
    doc = y.loads('''
# comment
name: demo            # trailing comment
count: 3
ratio: 0.5
flag: true
nothing: null
quoted: "a # not a comment, with: colon"
single: 'it''s'
inline: [one, "two, still two", 3]
nested:
  child:
    - alpha
    - "beta gamma"
  other: x
top_list:
- first
- second
''')
    assert doc == {"name": "demo", "count": 3, "ratio": 0.5, "flag": True, "nothing": None, "quoted": "a # not a comment, with: colon",
                   "single": "it's", "inline": ["one", "two, still two", 3],
                   "nested": {"child": ["alpha", "beta gamma"], "other": "x"}, "top_list": ["first", "second"]}


@pytest.mark.parametrize("text,fragment", [
    ("a:\n\t- x\n", "tabs"),
    ("a: &anchor x\n", "unsupported"),
    ("a: |\n  text\n", "unsupported"),
    ("a: {b: 1}\n", "unsupported"),
    ("a:\n  - k: v\n", "mappings are not supported"),
    ("a: 1\na: 2\n", "duplicate key"),
    ('a: "open\n', "unterminated"),
    ("a: b: c\n", "colon"),
    ("a:\n  b: 1\n c: 2\n", "indentation"),
    ("a: [1, 2\n", "inline list"),
])
def test_refuses_anything_outside_the_subset(text, fragment):
    with pytest.raises(y.YamlError, match=fragment):
        y.loads(text)


def test_yes_no_are_plain_strings_not_booleans():
    assert y.loads("a: yes\nb: no\n") == {"a": "yes", "b": "no"}          # YAML 1.2 behaviour: only true/false are booleans


def test_shipped_files_load_and_validate():
    p, r = prof.load_profile(), prof.load_ranking()
    assert list(p.families) == list(prof.ROLE_TYPES) and len(p.families) == 8
    assert p.status == "proposed_for_operator_review" and r["status"] == "proposed_for_operator_review"
    for fam in p.families.values():
        assert fam.strong and fam.re_strong is not None


def raw_profile():
    return copy.deepcopy(prof.load_profile().raw)


def test_unknown_missing_and_mistyped_keys_are_errors_not_silent_changes():
    raw = raw_profile()
    raw["role_famlies"] = raw.pop("role_families")
    with pytest.raises(prof.ConfigError):
        prof.build_profile(raw)
    raw = raw_profile()
    del raw["role_families"]["venture_studio"]
    with pytest.raises(prof.ConfigError):
        prof.build_profile(raw)
    raw = raw_profile()
    raw["role_families"]["founders_office"]["strong_aliases"] = []
    with pytest.raises(prof.ConfigError):
        prof.build_profile(raw)
    raw = raw_profile()
    raw["role_families"]["gtm_engineering"]["extra"] = ["x"]
    with pytest.raises(prof.ConfigError, match="unknown key"):
        prof.build_profile(raw)


def test_safety_invariants_cannot_be_edited_away():
    for path, value, fragment in [(("experience", "unknown_treatment"), "senior", "neutral"),
                                  (("unknown_handling", "location"), "approve", "needs_review"),
                                  (("verdicts", "never_modify_notes"), False, "never_modify_notes"),
                                  (("location", "other_india_cities"), "approved", "other_india_cities")]:
        raw = raw_profile()
        raw[path[0]][path[1]] = value
        with pytest.raises(prof.ConfigError, match=fragment):
            prof.build_profile(raw)
    raw = raw_profile()
    raw["role_families"]["ai_implementation"]["weak_aliases"].append(raw["role_families"]["ai_implementation"]["strong_aliases"][0])
    with pytest.raises(prof.ConfigError, match="both strong and weak"):
        prof.build_profile(raw)
    raw = raw_profile()
    raw["role_types"] = raw["role_types"][:-1]
    with pytest.raises(prof.ConfigError, match="eight"):
        prof.build_profile(raw)


def test_ranking_validation():
    r = copy.deepcopy(prof.load_ranking())
    r["jobs"]["thresholds"]["confirmed_eligible_min_score"] = 5
    with pytest.raises(prof.ConfigError, match="must not be below"):
        prof.build_ranking(r)
    r = copy.deepcopy(prof.load_ranking())
    del r["jobs"]["location"]["bengaluru"]
    with pytest.raises(prof.ConfigError, match="missing"):
        prof.build_ranking(r)
    r = copy.deepcopy(prof.load_ranking())
    r["companies"]["verdict"]["yes"] = "lots"
    with pytest.raises(prof.ConfigError, match="number"):
        prof.build_ranking(r)


def test_a_hand_edited_weight_is_picked_up(tmp_path):
    text = (prof.RANKING_PATH).read_text(encoding="utf-8").replace("    strong_alias: 40", "    strong_alias: 41")
    f = tmp_path / "ranking.yaml"
    f.write_text(text, encoding="utf-8")
    assert prof.load_ranking(f)["jobs"]["role_relevance"]["strong_alias"] == 41
