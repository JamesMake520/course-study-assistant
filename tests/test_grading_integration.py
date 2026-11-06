"""Scoring imports use synthetic rules only; no authenticated platform access."""
import copy

import pytest

from course_assistant.engine import Engine


@pytest.fixture
def engine(tmp_path):
    return Engine(tmp_path)


def sample():
    return {"course_name": "合成测试课程", "source_text": "累计12天，每天观看25分钟。", "target_score": 60,
            "total_score": 100, "warnings": [], "rules": [
                {"id": "video_days", "name": "有效学习天数", "metric": "video_days", "target": 12,
                 "daily_minutes": 25, "max_score": 10, "formula": "threshold", "points_per_unit": None,
                 "period": "total", "evidence": "合成测试：达到12天得10分，每天至少25分钟。"}]}


def save(e, rules=None, revision="", scope=None, reviewed=True):
    return e.action("grading_save", {"rules": rules or sample(), "scope": scope or e.scope,
                    "reviewed": reviewed, "expected_revision": revision})


def test_parse_does_not_save_or_change_execution_settings(engine):
    before = copy.deepcopy(engine.settings)
    draft = engine.action("grading_parse", {"text": "每天播放25分钟，累计12天；每天提问1条，回答1条。"})["draft"]
    assert draft["source_text"]
    assert engine.settings == before
    assert engine.state()["grading"]["saved"] is False


def test_review_required_and_unknown_scores_preserved(engine):
    with pytest.raises(ValueError, match="核对"):
        save(engine, reviewed=False)
    save(engine)
    g = engine.state()["grading"]
    assert g["saved"] and g["revision"]
    assert g["score"]["earned"] is None
    assert not g["score"]["complete"]


def test_rules_survive_restart_and_are_scoped(engine):
    save(engine)
    original = engine.state()["grading"]
    restored = Engine(engine.runtime_dir)
    assert restored.state()["grading"] == original
    restored.action("settings", {"settings": {"mode": "zhihuishu", "course_url": "https://study.zhihuishu.com/other"}})
    assert not restored.state()["grading"]["saved"]
    with pytest.raises(ValueError, match="课程已变化"):
        save(restored, scope=original["scope"])
    restored.action("settings", {"settings": {"mode": "demo", "course_url": ""}})
    assert restored.state()["grading"] == original


def test_stale_revision_and_bad_import_do_not_overwrite(engine):
    save(engine)
    original = engine.state()["grading"]
    with pytest.raises(ValueError, match="其他页面"):
        save(engine, revision="")
    broken = sample()
    broken["rules"][0]["target"] = True
    with pytest.raises(ValueError):
        save(engine, rules=broken, revision=original["revision"])
    with pytest.raises(ValueError):
        engine.action("grading_parse", {"text": '{"rules": [NaN]}', "format": "json"})
    assert engine.state()["grading"] == original
    altered = sample()
    altered["course_name"] = "更新后的合成课程"
    save(engine, rules=altered, revision=original["revision"])
    assert engine.state()["grading"]["revision"] != original["revision"]


def test_cross_instance_revision_guard(engine):
    another = Engine(engine.runtime_dir)
    save(engine)
    with pytest.raises(ValueError, match="其他页面"):
        save(another)


def test_empty_rules_are_not_a_saved_grading_scheme(engine):
    rules = sample()
    rules["rules"] = []
    with pytest.raises(ValueError, match="至少"):
        save(engine, rules=rules)


def test_concurrent_settings_change_cannot_relabel_saved_rules(engine, monkeypatch):
    save(engine)
    original_scope = engine.scope
    original_get = engine.store.get

    def change_after_read(key, default=None):
        result = original_get(key, default)
        engine.settings = engine.settings | {"course_url": "https://study.zhihuishu.com/other"}
        return result

    monkeypatch.setattr(engine.store, "get", change_after_read)
    state = engine.state()
    assert state["grading"]["scope"] == original_scope
    assert state["grading"]["saved"] is True
    assert state["settings"]["course_url"] == ""
    assert engine.scope != original_scope
