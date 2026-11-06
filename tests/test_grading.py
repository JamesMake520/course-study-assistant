"""Synthetic grading-rule examples; none are real-platform acceptance proof."""

import copy
import json
import math

import pytest

from course_assistant.grading import MAX_RULES, parse_rules, score_rules, validate_rules


def rule(**values):
    result = {
        "id": "rule_1", "name": "合成测试提问项", "metric": "questions", "target": 3,
        "daily_minutes": None, "max_score": 10, "formula": "threshold",
        "points_per_unit": None, "period": "total", "evidence": "合成测试：提问满3个得10分",
    }
    result.update(values)
    return result


def draft(*rows, **values):
    result = {"course_name": "合成课程", "source_text": "合成输入", "target_score": None, "total_score": 100, "rules": list(rows), "warnings": []}
    result.update(values)
    return result


def test_synthetic_daily_requirements_preserve_all_conditions_without_guessing_score():
    text = "每天播放25分钟，累计12天；每天提出一个问题和一个答案"
    parsed = parse_rules(text)
    assert parsed["source_text"] == text
    assert len(parsed["rules"]) == 3
    videos, questions, answers = parsed["rules"]
    assert (videos["metric"], videos["target"], videos["daily_minutes"], videos["period"]) == ("video_days", 12, 25, "total")
    assert (questions["metric"], questions["target"], questions["period"]) == ("questions", 1, "daily")
    assert (answers["metric"], answers["target"], answers["period"]) == ("answers", 1, "daily")
    assert all(row["formula"] == "unknown" and row["max_score"] is None for row in parsed["rules"])
    assert score_rules(parsed)["earned"] is None


def test_synthetic_maximum_and_days_do_not_imply_linear_or_threshold():
    parsed = parse_rules("视频学习12天，每天25分钟，合计10分")
    row = parsed["rules"][0]
    assert (row["target"], row["daily_minutes"], row["max_score"], row["formula"]) == (12, 25, 10, "unknown")
    assert score_rules(parsed, {row["id"]: 12})["earned"] is None


def test_synthetic_weights_do_not_turn_into_points_or_completion_targets():
    parsed = parse_rules("视频学习占20%；课程讨论权重10%")
    assert len(parsed["rules"]) == 2
    assert parsed["total_score"] is None
    assert all(row["max_score"] is None and row["target"] is None for row in parsed["rules"])
    assert any("百分比" in warning for warning in parsed["warnings"])


def test_synthetic_simple_explicit_threshold_is_recognized():
    parsed = parse_rules("课程名称：合成计算机课程\n总分：100分\n目标分数：85分\n提问满三次得十分；视频完成率达到80%得20分")
    assert (parsed["course_name"], parsed["total_score"], parsed["target_score"]) == ("合成计算机课程", 100, 85)
    assert [row["formula"] for row in parsed["rules"]] == ["threshold", "threshold"]
    assert [row["target"] for row in parsed["rules"]] == [3, 80]
    assert [row["max_score"] for row in parsed["rules"]] == [10, 20]


def test_synthetic_per_unit_keeps_complete_blocks_and_cap():
    parsed = parse_rules("每3次提问计2分，上限10分")
    row = parsed["rules"][0]
    assert (row["formula"], row["target"], row["points_per_unit"], row["max_score"]) == ("per_unit", 3, 2, 10)
    assert score_rules(parsed, {"rule_1": 1})["earned"] == 0
    assert score_rules(parsed, {"rule_1": 8})["earned"] == 4
    assert score_rules(parsed, {"rule_1": 99})["earned"] == 10


@pytest.mark.parametrize("text", ["每回答1个问题计2分", "每条答案得2分", "每次回答计2分"])
def test_synthetic_answering_a_question_is_not_two_metrics(text):
    parsed = parse_rules(text)
    assert len(parsed["rules"]) == 1
    assert parsed["rules"][0]["metric"] == "answers"
    assert parsed["rules"][0]["formula"] == "per_unit"
    assert parsed["rules"][0]["max_score"] is None


@pytest.mark.parametrize("text", ["提问满3个且回答满3个得10分", "视频完成率达到80%，累计学习12天得10分"])
def test_synthetic_shared_rewards_are_not_duplicated(text):
    parsed = parse_rules(text)
    assert len(parsed["rules"]) == 2
    assert all(row["formula"] == "unknown" and row["max_score"] is None for row in parsed["rules"])
    assert all(row["evidence"] == text for row in parsed["rules"])
    assert any("共同条件" in warning for warning in parsed["warnings"])


@pytest.mark.parametrize("text", ["提问满3个且被教师采纳1个得10分", "提问满3个，浏览10次得10分", "连续提问满3个得10分", "提问满3个得10分，否则扣2分"])
def test_synthetic_extra_conditions_never_disappear_into_a_simple_formula(text):
    parsed = parse_rules(text)
    assert parsed["rules"][0]["formula"] == "unknown"
    assert parsed["rules"][0]["evidence"] == text
    assert any(text in warning for warning in parsed["warnings"])


def test_synthetic_plain_chinese_numbers_fullwidth_and_markdown():
    parsed = parse_rules("# 课程名称：合成课程\n- 每天播放二十五分钟，累计十二天\n- 提问满３次得１０分", "md")
    assert parsed["rules"][0]["target"] == 12
    assert parsed["rules"][0]["daily_minutes"] == 25
    assert parsed["rules"][1]["formula"] == "threshold"


@pytest.mark.parametrize("text", ["", "  \n ", "由老师另行通知，请留意班级公告。"])
def test_synthetic_empty_or_unrecognized_text_preserves_source_and_warns(text):
    parsed = parse_rules(text)
    assert parsed["source_text"] == text
    assert parsed["rules"] == []
    assert parsed["warnings"]
    assert score_rules(parsed)["earned"] is None


def test_synthetic_unrecognized_fragment_is_visible():
    parsed = parse_rules("提问满3次得10分；特殊申请需教师审批")
    assert any("未识别原文：特殊申请需教师审批" == warning for warning in parsed["warnings"])


def test_synthetic_long_paragraph_is_retained_in_source_with_warning():
    text = "视频学习" + "补充条件" * 400
    parsed = parse_rules(text)
    assert parsed["source_text"] == text
    assert parsed["rules"] == []
    assert any("1500" in warning for warning in parsed["warnings"])


def test_synthetic_rule_limit_is_explicit_and_original_remains_available():
    text = "；".join("提问满3次得10分" for _ in range(MAX_RULES + 2))
    parsed = parse_rules(text)
    assert len(parsed["rules"]) == MAX_RULES
    assert parsed["source_text"] == text
    assert any("超过30条" in warning for warning in parsed["warnings"])


def test_json_roundtrip_and_validation_do_not_mutate_input():
    data = draft(rule())
    original = copy.deepcopy(data)
    validated = validate_rules(data)
    validated["rules"][0]["name"] = "修改副本"
    assert data == original
    assert parse_rules(json.dumps(data, ensure_ascii=False), "json") == validate_rules(data)


def test_synthetic_large_valid_export_can_be_reimported():
    data = draft(*(rule(id=f"r{i}", evidence="原" * 1500) for i in range(30)), source_text="文" * 20000, total_score=None)
    exported = json.dumps(data, ensure_ascii=False)
    assert len(exported) > 20000
    assert parse_rules(exported, "json") == validate_rules(data)


@pytest.mark.parametrize("text", ['[]', '{"command":"run"}', '{"rules":[{"name":"合成","script":"run"}]}', '{"rules":[],"rules":[]}', '{"total_score":NaN}', '{"total_score":Infinity}', '{"total_score":-Infinity}', '{bad json}'])
def test_json_rejects_unsupported_or_unsafe_structures(text):
    with pytest.raises(ValueError):
        parse_rules(text, "json")


@pytest.mark.parametrize("value", [True, False, "10", math.nan, math.inf, -math.inf, -1, 10001, 10 ** 400])
def test_json_scores_reject_bool_strings_nonfinite_and_out_of_range(value):
    with pytest.raises(ValueError):
        validate_rules(draft(rule(max_score=value)))


@pytest.mark.parametrize("updates", [{"metric": "arbitrary"}, {"formula": "eval"}, {"period": "weekly"}, {"target": True}, {"target": -1}, {"target": 0}, {"target": 1.5}, {"daily_minutes": 1441}, {"points_per_unit": math.nan}, {"name": " "}, {"id": "<script>"}, {"evidence": "x" * 1501}, {"formula": ["unknown"]}], ids=["metric", "formula", "period", "bool-target", "negative-target", "zero-target", "fractional-count", "minutes", "nan-points", "empty-name", "id", "long-evidence", "formula-list"])
def test_json_rule_fields_are_strictly_validated(updates):
    with pytest.raises(ValueError):
        validate_rules(draft(rule(**updates)))


def test_json_duplicates_limits_and_impossible_targets_are_rejected():
    for data in [draft(rule(), rule()), draft(*[rule(id=f"r{i}") for i in range(31)]), draft(rule(metric="video_completion", target=101)), draft(rule(), source_text="x" * 20001), draft(rule(), target_score=101), draft(rule(), warnings="not a list")]:
        with pytest.raises(ValueError):
            validate_rules(data)


@pytest.mark.parametrize("text,format", [("x" * 20001, "text"), ("x" * 160001, "json"), ("x", "yaml"), (42, "text"), ("", ["text"])], ids=["oversized-text", "oversized-json", "unsupported-format", "nontext", "invalid-format-type"])
def test_oversized_and_unsupported_imports_are_rejected(text, format):
    with pytest.raises(ValueError):
        parse_rules(text, format)


def test_json_byte_limit_and_deep_nesting_are_rejected():
    with pytest.raises(ValueError, match="字节"):
        parse_rules('"' + "😀" * 130000 + '"', "json")
    with pytest.raises(ValueError):
        parse_rules("[" * 2000 + "]" * 2000, "json")


def test_synthetic_missing_platform_progress_stays_unknown():
    result = score_rules(draft(rule()))
    assert result["earned"] is None
    assert result["complete"] is False
    assert result["known_score"] == 0
    assert result["items"][0]["score"] is None


def test_synthetic_threshold_linear_and_total_caps():
    data = draft(rule(), rule(id="rule_2", metric="video_completion", target=80, formula="linear", max_score=20), total_score=25)
    result = score_rules(data, {"rule_1": 3, "rule_2": 100})
    assert result["earned"] == 25
    assert result["complete"] is True
    assert [item["score"] for item in result["items"]] == [10, 20]
    assert score_rules(data, {"rule_1": 2, "rule_2": 40})["earned"] == 10


def test_synthetic_known_subtotal_is_not_a_complete_grade():
    data = draft(rule(), rule(id="unknown", formula="unknown"))
    result = score_rules(data, {"rule_1": 3, "unknown": 9})
    assert result["earned"] is None
    assert result["known_score"] == 10
    assert result["complete"] is False


@pytest.mark.parametrize("updates", [{"period": "daily"}, {"metric": "video_days", "daily_minutes": 25}])
def test_synthetic_daily_counts_and_minutes_need_additional_evidence(updates):
    result = score_rules(draft(rule(**updates)), {"rule_1": 100})
    assert result["earned"] is None
    assert result["items"][0]["score"] is None


@pytest.mark.parametrize("value", [True, math.nan, math.inf, -1, 1.5, "3", {"drafts": 3}])
def test_invalid_progress_cannot_become_a_grade(value):
    with pytest.raises(ValueError):
        score_rules(draft(rule()), {"rule_1": value})


def test_local_draft_metadata_is_not_progress():
    result = score_rules(draft(rule()), {"drafts": 3, "published": 3, "questions": 3})
    assert result["earned"] is None


@pytest.mark.parametrize("text", ["视频学习天数达到12天得10分；每天至少25分钟。", "提问达到3条得5分；必须审核通过才计分", "提问达到3条得5分；此规则仅适用于第一周", "提问达到3条得5分；提问必须被采纳", "提问达到3条得5分；作业迟交扣5分"])
def test_synthetic_cross_sentence_conditions_do_not_leave_automatic_formulas(text):
    parsed = parse_rules(text)
    assert all(row["formula"] == "unknown" for row in parsed["rules"])
    assert score_rules(parsed, {row["id"]: 12 for row in parsed["rules"]})["known_score"] == 0
    assert any("未完整解析" in warning for warning in parsed["warnings"])


@pytest.mark.parametrize("text", ["作业迟交扣5分", "视频学习需要10分努力", "提问满3次得10分，否则扣2分"])
def test_synthetic_isolated_or_negative_points_are_not_maxima(text):
    parsed = parse_rules(text)
    assert all(row["max_score"] is None for row in parsed["rules"])


def test_fractional_complete_units_use_decimal_arithmetic():
    parsed = draft(rule(metric="other", formula="per_unit", target=0.1, points_per_unit=1))
    assert score_rules(parsed, {"rule_1": 0.3})["earned"] == 3


def test_unsupported_tiny_scoring_unit_is_rejected_before_calculation():
    with pytest.raises(ValueError, match="0.000001"):
        score_rules(draft(rule(metric="other", formula="per_unit", target=5e-324, points_per_unit=1)), {"rule_1": 1})


@pytest.mark.parametrize("field", ["course_name", "source_text", "warnings", "name", "evidence"])
def test_escaped_surrogates_are_rejected_before_http_or_storage(field):
    data = draft(rule())
    if field in {"name", "evidence"}:
        data["rules"][0][field] = "\ud800"
    else:
        data[field] = ["\ud800"] if field == "warnings" else "\ud800"
    with pytest.raises(ValueError, match="Unicode"):
        parse_rules(json.dumps(data), "json")


def test_normalized_export_size_is_bounded_for_roundtripping():
    data = draft(rule(), warnings=[f"{i}:" + "文" * 1900 for i in range(100)])
    with pytest.raises(ValueError, match="导出后可再次导入"):
        validate_rules(data)
