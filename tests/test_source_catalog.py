import copy

import pytest

from ppt_rubric.source_catalog import source_catalog, resolve_source_ids


def test_catalog_preserves_chinese_table_rows_headings_and_source_offsets():
    original = ("  老旧小区改造工作汇报\r\n\r\n"
                "| 指标 | 2025年 | 2026年 |\r\n"
                "| 到位资金（万元） | 1,000 | 1,200 |\r\n"
                "| 执行率 | 90% | 86% |\r\n"
                " \t\r\n"
                "注意：两项比率的分母不同，不得直接比较。  \n"
                "存疑的地方标出来,别下定论；保留“约”和来源。")
    catalog = source_catalog([{"id": "prompt", "text": original}])
    assert len(catalog) == 6
    assert catalog[0]["text"] == "  老旧小区改造工作汇报"
    assert catalog[-2]["text"].endswith("。  ")
    assert catalog[-1]["text"] == "存疑的地方标出来,别下定论；保留“约”和来源。"
    covered = set()
    for fragment in catalog:
        assert fragment["source_id"] == "prompt"
        assert fragment["text"] == original[fragment["start"]:fragment["end"]]
        positions = set(range(fragment["start"], fragment["end"]))
        assert not (covered & positions)
        covered.update(positions)
    assert all(index in covered for index, char in enumerate(original) if not char.isspace())
    assert all("\r" not in item["text"] and "\n" not in item["text"] for item in catalog)


def test_catalog_ids_are_deterministic_and_distinguish_sources_and_repeated_rows():
    sources = [{"id": "prompt", "text": "相同原文\n相同原文"},
               {"id": "attachment-1", "text": "相同原文"}]
    snapshot = copy.deepcopy(sources)
    catalog = source_catalog(sources)
    assert source_catalog(sources) == catalog
    assert sources == snapshot
    assert len({item["id"] for item in catalog}) == 3
    reverse = source_catalog(list(reversed(sources)))
    assert {item["id"] for item in reverse} == {item["id"] for item in catalog}
    changed = source_catalog([{"id": "prompt", "text": "相同原文\n不同原文"}])
    assert changed[0]["id"] == catalog[0]["id"]
    assert changed[1]["id"] != catalog[1]["id"]


def test_long_line_splits_only_at_natural_boundaries_without_omission_or_overlap():
    sentence = "按本期已到位资金计算执行率，不能同预算总额相比。"
    original = sentence * 180 + "  尾部说明不删除。  "
    catalog = source_catalog([{"id": "prompt", "text": original}])
    assert len(catalog) > 1
    assert "".join(item["text"] for item in catalog) == original
    assert catalog[0]["start"] == 0
    assert catalog[-1]["end"] == len(original)
    for left, right in zip(catalog, catalog[1:]):
        assert left["end"] == right["start"]
        assert left["text"].endswith("。")


def test_long_unbroken_formula_is_not_truncated_or_split_inside_number():
    original = "28.125%=" + "90/320+" * 400 + "0"
    catalog = source_catalog([{"id": "prompt", "text": original}])
    assert len(catalog) == 1
    assert catalog[0]["text"] == original


def test_unicode_and_whitespace_are_never_normalized():
    original = 'e\u0301 ≠ é；\t“1,000” ≠ "1，000"  '
    catalog = source_catalog([{"id": "附 件", "text": original}])
    assert len(catalog) == 1
    assert catalog[0]["text"].encode("utf-8") == original.encode("utf-8")
    assert catalog[0]["end"] == len(original)


def test_resolver_keeps_noncontiguous_rows_separate_and_returns_copies():
    catalog = source_catalog([{"id": "prompt", "text": "标题\n第一行\n第二行\n第三行"}])
    selected = resolve_source_ids(catalog, [catalog[1]["id"], catalog[3]["id"]])
    assert [item["text"] for item in selected] == ["第一行", "第三行"]
    assert selected[0]["end"] < selected[1]["start"]
    selected[0]["text"] = "不能反写来源"
    assert catalog[1]["text"] == "第一行"
    assert resolve_source_ids(catalog, []) == []


@pytest.mark.parametrize("ids, error", [
    (["fabricated"], "Unknown source"),
    ("not-a-list", "must be a list"),
    ([None], "must be a list"),
    ([1], "must be a list"),
    ([""], "must be a list"),
])
def test_resolver_rejects_invalid_references(ids, error):
    catalog = source_catalog([{"id": "prompt", "text": "原文"}])
    with pytest.raises(ValueError, match=error):
        resolve_source_ids(catalog, ids)


def test_resolver_rejects_duplicate_selections_and_ambiguous_catalog():
    catalog = source_catalog([{"id": "prompt", "text": "原文"}])
    ref = catalog[0]["id"]
    with pytest.raises(ValueError, match="Duplicate source reference"):
        resolve_source_ids(catalog, [ref, ref])
    with pytest.raises(ValueError, match="Duplicate source catalog"):
        resolve_source_ids(catalog + catalog, [ref])


@pytest.mark.parametrize("sources", [
    None, {}, [None], [{"id": "", "text": "text"}],
    [{"id": "prompt", "text": None}],
    [{"id": "prompt", "text": "first"}, {"id": "prompt", "text": "second"}],
])
def test_catalog_rejects_invalid_or_ambiguous_sources(sources):
    with pytest.raises(ValueError):
        source_catalog(sources)


def test_empty_sources_and_blank_lines_have_no_fragments():
    assert source_catalog([]) == []
    assert source_catalog([{"id": "prompt", "text": "\r\n \t\n"}]) == []
