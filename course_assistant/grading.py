"""Local, conservative grading-rule drafts; never queries a platform or an AI.

Text parsing recognizes a small set of Chinese templates, not arbitrary natural
language. The original text remains available for the user's confirmation.
``per_unit`` means a complete block: floor(progress / target) * points_per_unit.
Progress, when supplied, must be independently verified by the caller and keyed
by rule id. Local drafts and publication attempts are not progress evidence.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from decimal import Decimal, ROUND_FLOOR, localcontext


MAX_SOURCE_LENGTH = 20_000
MAX_JSON_LENGTH = 160_000
MAX_JSON_BYTES = 500_000
MAX_RULES = 30
MAX_EVIDENCE_LENGTH = 1_500
MAX_WARNINGS = 100
MAX_SCORE = 10_000
MAX_TARGET = 1_000_000
METRICS = {"video_days", "video_completion", "questions", "answers", "other"}
FORMULAS = {"unknown", "threshold", "linear", "per_unit"}
PERIODS = {"total", "daily"}
ROOT_FIELDS = {"course_name", "source_text", "target_score", "total_score", "rules", "warnings"}
RULE_FIELDS = {
    "id", "name", "metric", "target", "daily_minutes", "max_score", "formula",
    "points_per_unit", "period", "evidence",
}
METRIC_NAMES = {
    "video_days": "视频学习天数", "video_completion": "视频完成率",
    "questions": "提问数量", "answers": "回答数量", "other": "其他评分项",
}
NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+(?:点[零〇一二两三四五六七八九]+)?)"
UNIT = r"(?:个|条|次|道|题)?"
THRESHOLD = r"(?:达到|达|满|至少|不少于)"
REWARD = r"(?:即可|可|则)?(?:获得|奖励|得|计|加)"


def _text(value, label, limit, *, nonempty=False):
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是文字")
    if len(value) > limit:
        raise ValueError(f"{label}不能超过{limit}个字符")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label}包含无效Unicode字符") from exc
    if nonempty and not value.strip():
        raise ValueError(f"{label}不能为空")
    return value


def _number(value, label, maximum, *, positive=False, integer=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须是数字或null，不能使用布尔值或数字字符串")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < 0 or value > maximum or (positive and value <= 0):
        minimum = "大于0且" if positive else ""
        raise ValueError(f"{label}必须{minimum}在0到{maximum}之间，且为有限数字")
    if integer and value != int(value):
        raise ValueError(f"{label}必须是整数")
    return int(value) if value == int(value) else value


def _warn(warnings, message):
    if message not in warnings and len(warnings) < MAX_WARNINGS:
        warnings.append(message)


def validate_rules(data: dict) -> dict:
    """Return a new normalized draft or raise ValueError; never mutate input."""
    if not isinstance(data, dict):
        raise ValueError("评分规则JSON顶层必须是对象")
    if set(data) - ROOT_FIELDS:
        raise ValueError("评分规则包含不支持的顶层字段")
    course = _text(data.get("course_name", ""), "课程名称", 200).strip()
    source = _text(data.get("source_text", ""), "评分原文", MAX_SOURCE_LENGTH)
    total = _number(data.get("total_score"), "课程总分", MAX_SCORE, positive=True)
    target_score = _number(data.get("target_score"), "目标分数", MAX_SCORE)
    if total is not None and target_score is not None and target_score > total:
        raise ValueError("目标分数不能超过课程总分")
    rows = data.get("rules", [])
    if not isinstance(rows, list) or len(rows) > MAX_RULES:
        raise ValueError(f"评分项必须是数组且不能超过{MAX_RULES}条")
    supplied_warnings = data.get("warnings", [])
    if not isinstance(supplied_warnings, list) or len(supplied_warnings) > MAX_WARNINGS:
        raise ValueError(f"提醒必须是数组且不能超过{MAX_WARNINGS}条")
    warnings = []
    for warning in supplied_warnings:
        _warn(warnings, _text(warning, "提醒", 2_000, nonempty=True))
    normalized = []
    seen = set()
    for index, row in enumerate(rows, 1):
        label = f"第{index}条评分项"
        if not isinstance(row, dict) or set(row) - RULE_FIELDS:
            raise ValueError(f"{label}必须是仅含受支持字段的对象")
        rule_id = row.get("id", f"rule_{index}")
        if not isinstance(rule_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rule_id):
            raise ValueError(f"{label}的id仅允许1到64位字母、数字、下划线或连字符")
        if rule_id in seen:
            raise ValueError("评分项id不能重复")
        seen.add(rule_id)
        metric = row.get("metric", "other")
        formula = row.get("formula", "unknown")
        period = row.get("period", "total")
        for value, allowed, field in ((metric, METRICS, "指标"), (formula, FORMULAS, "计分方式"), (period, PERIODS, "统计周期")):
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f"{label}的{field}不受支持")
        target = _number(
            row.get("target"), f"{label}的目标/计分单位", 100 if metric == "video_completion" else MAX_TARGET,
            positive=formula != "unknown", integer=metric in {"questions", "answers", "video_days"},
        )
        if formula != "unknown" and target is not None and target < 0.000001:
            raise ValueError(f"{label}的目标/计分单位不能小于0.000001")
        minutes = _number(row.get("daily_minutes"), f"{label}的每日分钟数", 1_440, positive=True)
        maximum = _number(row.get("max_score"), f"{label}的满分", MAX_SCORE)
        points = _number(row.get("points_per_unit"), f"{label}的单位分值", MAX_SCORE)
        if formula in {"threshold", "linear"} and (target is None or maximum is None):
            raise ValueError(f"{label}使用阈值或线性计分时必须填写目标与满分")
        if formula == "per_unit" and (target is None or points is None):
            raise ValueError(f"{label}按单位计分时必须填写计分单位与单位分值")
        normalized.append({
            "id": rule_id,
            "name": _text(row.get("name", ""), f"{label}名称", 200, nonempty=True).strip(),
            "metric": metric, "target": target, "daily_minutes": minutes,
            "max_score": maximum, "formula": formula, "points_per_unit": points,
            "period": period,
            "evidence": _text(row.get("evidence", ""), f"{label}的原文证据", MAX_EVIDENCE_LENGTH),
        })
        if formula == "unknown":
            _warn(warnings, f"{rule_id}：计分方式尚未明确，请对照原文确认。")
        if period == "daily":
            _warn(warnings, f"{rule_id}：每日目标不能直接换算成整门课程得分。")
    if not normalized:
        _warn(warnings, "尚未识别到评分项，请保留原文并手动补充。")
    if total is not None and sum(row["max_score"] or 0 for row in normalized) > total:
        _warn(warnings, "各项满分合计超过课程总分，请检查重复项、共同条件或权重。")
    result = {
        "course_name": course, "source_text": source, "target_score": target_score,
        "total_score": total, "rules": normalized, "warnings": warnings,
    }
    exported = json.dumps(result, ensure_ascii=False, indent=2)
    if len(exported) > MAX_JSON_LENGTH or len(exported.encode("utf-8")) > 480_000:
        raise ValueError("规则、原文和提醒合计过大，请减少重复提醒或拆分评分项，以保证导出后可再次导入")
    return result


def _chinese_number(raw):
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        value = float(raw)
        return int(value) if value.is_integer() else value
    digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    if "点" in raw:
        whole, fraction = raw.split("点", 1)
        return float(str(_chinese_number(whole)) + "." + "".join(str(digits[c]) for c in fraction))
    if all(char in digits for char in raw):
        return int("".join(str(digits[c]) for c in raw))
    subtotal = 0
    digit = 0
    total = 0
    for char in raw:
        if char in digits:
            digit = digits[char]
        elif char == "万":
            total += (subtotal + digit) * 10_000
            subtotal = digit = 0
        else:
            subtotal += (digit or 1) * {"十": 10, "百": 100, "千": 1_000}[char]
            digit = 0
    return total + subtotal + digit


def _find_number(pattern, text):
    match = re.search(pattern, text)
    return _chinese_number(match.group("number")) if match else None


def _metrics(text):
    result = []
    video = bool(re.search(r"视频|播放|观看|学习.{0,8}(?:天|分钟)", text))
    if video and (re.search(NUMBER + r"天", text) or ("分钟" in text and re.search(r"每天|每日", text))):
        result.append("video_days")
    if video and (re.search(r"完成率|完成度|进度|%|百分之", text) or not result):
        result.append("video_completion")
    question = bool(re.search(r"提问|(?:提出|提交|发布|发表)[^，,]{0,12}问题", text))
    if not question:
        for match in re.finditer("问题", text):
            # “回答一个问题” describes an answer, not a second question item.
            prefix = text[max(0, match.start() - 12):match.start()]
            if not re.search(r"回答|答复|回复", prefix):
                question = True
                break
    if question:
        result.append("questions")
    if re.search(r"回答|答案|答复|回复", text):
        result.append("answers")
    if not result and re.search(r"考试|测验|测评|作业|签到|互动|讨论|得分|成绩|权重", text):
        result.append("other")
    return result


def _target(metric, text):
    if metric == "video_days":
        return _find_number(r"(?P<number>" + NUMBER + r")天", text)
    if metric == "video_completion":
        # A weight such as 视频占20% is not a completion target or 20 points.
        return _find_number(
            r"(?:完成率|完成度|进度|完成|达到|达|满)(?:为|是|达到|达|满|至少|不少于|:|：)?(?P<number>" + NUMBER + r")%", text,
        )
    if metric in {"questions", "answers"}:
        action = r"(?:提问|提出|提交|发布|发表)" if metric == "questions" else r"(?:回答|答复|回复)"
        noun = r"(?:问题|提问)" if metric == "questions" else r"(?:答案|回答|答复|回复)"
        patterns = [
            action + r"(?:问题|答案|数|数量)?(?:达到|达|满|至少|不少于|:|：)?(?P<number>" + NUMBER + ")" + UNIT,
            r"(?P<number>" + NUMBER + ")" + UNIT + noun,
            noun + r"(?:数|数量)?(?:达到|达|满|至少|不少于|:|：)?(?P<number>" + NUMBER + ")" + UNIT,
        ]
        for pattern in patterns:
            value = _find_number(pattern, text)
            if value is not None:
                return value
    return None


def _explicit_formula(metric, text):
    """Only full, simple templates can introduce a calculable formula."""
    body = text.strip(" ，,：:")
    cap = None
    cap_match = re.search(r"[,，]?(?:上限|最高(?:得)?|最多(?:可得|得)?|满分)(?P<number>" + NUMBER + r")分$", body)
    if cap_match:
        cap = _chinese_number(cap_match.group("number"))
        body = body[:cap_match.start()].rstrip(" ，,")
    qty = r"(?P<qty>" + NUMBER + ")"
    points = r"(?P<points>" + NUMBER + ")"
    patterns = []
    if metric in {"questions", "answers"}:
        action = r"(?:提问|提出|发布|发表)" if metric == "questions" else r"(?:回答|答复|回复)"
        noun = r"(?:问题|提问)" if metric == "questions" else r"(?:答案|回答|答复|回复|问题)"
        # Two separate patterns avoid duplicate named groups in an alternation.
        patterns = [r"每" + qty + UNIT + noun, r"每" + action + qty + UNIT + r"(?:问题|答案)?"]
        implicit = [r"每(?:个|条|次|道)" + noun, r"每" + action + r"(?:一次|一条|一个问题)?"]
    elif metric == "video_days":
        patterns = [r"每(?:观看|播放|学习)?" + qty + r"天"]
        implicit = [r"每(?:观看|播放|学习)?天"]
    else:
        implicit = []
    for pattern in patterns + implicit:
        match = re.fullmatch(pattern + REWARD + points + r"分", body)
        if match:
            return {
                "formula": "per_unit", "target": _chinese_number(match.groupdict().get("qty") or "1"),
                "points_per_unit": _chinese_number(match.group("points")), "max_score": cap,
            }
    # A threshold's reward is its maximum. An additional distinct cap cannot
    # be represented without knowing the omitted tiers, so leave it unknown.
    if cap is not None:
        return None
    if metric in {"questions", "answers"}:
        action = r"(?:提问|提出问题|发布问题|问题)" if metric == "questions" else r"(?:回答|答复|回复|答案)"
        pattern = r"(?:累计|累积|总计)?" + action + r"(?:数|数量)?" + THRESHOLD + qty + UNIT
    elif metric == "video_days":
        pattern = r"(?:累计|累积)?(?:视频)?(?:观看|播放|学习)(?:天数)?" + THRESHOLD + qty + "天"
    elif metric == "video_completion":
        pattern = r"(?:视频)?(?:完成率|完成度|进度)" + THRESHOLD + qty + "%"
    else:
        return None
    match = re.fullmatch(pattern + REWARD + points + r"分", body)
    if match:
        return {"formula": "threshold", "target": _chinese_number(match.group("qty")), "points_per_unit": None, "max_score": _chinese_number(match.group("points"))}
    # Linear scaling is never inferred from a target and a maximum alone.
    return None


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("评分规则JSON不能包含重复字段")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("评分规则JSON不允许NaN或Infinity")


def parse_rules(text: str, format: str = "text") -> dict:
    """Import a local text/JSON draft. No formula is guessed from a weight."""
    if not isinstance(format, str) or format.lower() not in {"text", "txt", "md", "markdown", "json"}:
        raise ValueError("只支持文字、TXT、Markdown或评分规则JSON")
    is_json = format.lower() == "json"
    _text(text, "导入内容", MAX_JSON_LENGTH if is_json else MAX_SOURCE_LENGTH)
    if is_json:
        try:
            encoded_length = len(text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("评分规则JSON包含无效Unicode字符") from exc
        if encoded_length > MAX_JSON_BYTES:
            raise ValueError("评分规则JSON的UTF-8大小不能超过500000字节")
        try:
            data = json.loads(text.lstrip("\ufeff"), object_pairs_hook=_json_pairs, parse_constant=_reject_constant)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("评分规则JSON格式不正确或嵌套过深") from exc
        return validate_rules(data)
    draft = {"course_name": "", "source_text": text, "target_score": None, "total_score": None, "rules": [], "warnings": []}
    warnings = draft["warnings"]
    needs_context_review = False
    for original in re.split(r"[\r\n；;。]+", text):
        evidence = original.strip()
        if not evidence:
            continue
        if len(evidence) > MAX_EVIDENCE_LENGTH:
            _warn(warnings, "有段落超过1500字符，已完整保留在评分原文中，请分段或手动整理后确认。")
            needs_context_review = True
            continue
        normalized = unicodedata.normalize("NFKC", evidence)
        normalized = re.sub(r"^\s*(?:[-*#]+\s*|\d+[.)、]\s*)", "", normalized)
        compact = re.sub(r"\s+", "", normalized).rstrip(".")
        course_match = re.fullmatch(r"(?:课程名称|课程名|课程)[:：](.+)", compact)
        if course_match:
            if draft["course_name"] and draft["course_name"] != course_match.group(1):
                _warn(warnings, "原文出现多个课程名称，请确认当前课程。")
            else:
                draft["course_name"] = course_match.group(1)
            continue
        metadata = False
        for key, prefix in (("total_score", r"(?:课程总分|课程满分|总分|满分)"), ("target_score", r"(?:目标分数|目标成绩|目标总分|目标分)")):
            match = re.fullmatch(prefix + r"(?:为|是|:|：)?(?P<number>" + NUMBER + r")分?", compact)
            if match:
                value = _chinese_number(match.group("number"))
                if draft[key] is not None and draft[key] != value:
                    _warn(warnings, "原文出现不一致的总分或目标分数，请手动确认。")
                    draft[key] = None
                else:
                    draft[key] = value
                metadata = True
                break
        if metadata:
            continue
        metrics = _metrics(compact)
        if not metrics:
            _warn(warnings, "未识别原文：" + evidence)
            needs_context_review = True
            continue
        combination = len(metrics) > 1
        if combination:
            _warn(warnings, "同一句包含多个指标或共同条件，已分别保留；共同分值未拆分，请核对原文：" + evidence)
        if re.search(r"(?:占|权重|比例).{0,12}%", compact):
            _warn(warnings, "百分比可能是成绩权重，未直接当作分数或完成目标：" + evidence)
        for metric in metrics:
            if len(draft["rules"]) >= MAX_RULES:
                _warn(warnings, "识别到的评分项超过30条，其余内容保留在评分原文中，请分批整理。")
                needs_context_review = True
                break
            daily = bool(re.search(r"每天|每日|每个学习日", compact))
            minutes = _find_number(r"(?P<number>" + NUMBER + r")分钟", compact) if daily and metric == "video_days" else None
            target = _target(metric, compact)
            maximum = None
            if not combination:
                maximum = _find_number(r"(?:满分|上限|最高(?:得)?|最多(?:可得|得)?|合计|共计|总计)(?:为|是|:|：)?(?P<number>" + NUMBER + r")分", compact)
            row = {
                "id": f"rule_{len(draft['rules']) + 1}", "name": METRIC_NAMES[metric],
                "metric": metric, "target": target, "daily_minutes": minutes, "max_score": maximum,
                "formula": "unknown", "points_per_unit": None,
                "period": "total" if metric == "video_days" and target is not None else ("daily" if daily else "total"),
                "evidence": evidence,
            }
            explicit = None if combination else _explicit_formula(metric, compact)
            if explicit is not None:
                row.update(explicit)
            else:
                _warn(warnings, "未完整识别计分方式，条件与未识别文字保留在原文证据中：" + evidence)
                if re.search(r"必须|须|审核|采纳|仅|才|扣|连续|否则|另外|同时|并且|且", compact):
                    needs_context_review = True
            draft["rules"].append(row)
    if needs_context_review:
        downgraded = False
        for row in draft["rules"]:
            if row["formula"] != "unknown":
                row["formula"] = "unknown"
                downgraded = True
        if downgraded:
            _warn(warnings, "存在未完整解析的条款，可能限制其他评分项；自动识别的计分方式已改为待确认，请核对全部条件。")
    return validate_rules(draft)


def score_rules(rules: dict, progress: dict | None = None) -> dict:
    """Conservative estimate from confirmed rules and caller-verified progress.

    ``earned`` is null whenever any item cannot be computed. ``known_score``
    is only the subtotal of computable items, never a platform-issued grade.
    Daily totals and per-day video-minute conditions need evidence that this
    simple numeric progress format cannot represent, so remain unknown.
    """
    draft = validate_rules(rules)
    if progress is not None and not isinstance(progress, dict):
        raise ValueError("已核实进度必须是按评分项id索引的对象")
    progress = progress or {}
    items = []
    known = 0.0
    warnings = []
    for row in draft["rules"]:
        score = None
        reason = ""
        value = progress.get(row["id"])
        if row["formula"] == "unknown":
            reason = "计分方式尚未确认"
        elif row["period"] == "daily":
            reason = "每日记录不足以核实整门课程的累计得分"
        elif row["daily_minutes"] is not None:
            reason = "尚无逐日学习分钟数的核验证据"
        elif value is None:
            reason = "没有可信的平台进度证据"
        else:
            value = _number(value, f"{row['id']}的已核实进度", 100 if row["metric"] == "video_completion" else MAX_TARGET,
                            integer=row["metric"] in {"video_days", "questions", "answers"})
            if row["formula"] == "threshold":
                score = row["max_score"] if value >= row["target"] else 0
            elif row["formula"] == "linear":
                score = row["max_score"] * min(value / row["target"], 1)
            else:
                with localcontext() as context:
                    context.prec = 50
                    units = (Decimal(str(value)) / Decimal(str(row["target"]))).to_integral_value(rounding=ROUND_FLOOR)
                    calculated = units * Decimal(str(row["points_per_unit"]))
                    if row["max_score"] is not None:
                        calculated = min(calculated, Decimal(str(row["max_score"])))
                    score = float(calculated)
            score = round(score, 6)
            known += score
            reason = "依据已确认规则与调用方提供的可信进度估算"
        if score is None:
            _warn(warnings, f"{row['name']}：{reason}。")
        items.append({"id": row["id"], "name": row["name"], "score": score, "max_score": row["max_score"], "reason": reason})
    if draft["total_score"] is not None:
        known = min(known, draft["total_score"])
    known = round(known, 6)
    complete = bool(items) and all(item["score"] is not None for item in items)
    if not items:
        _warn(warnings, "没有可计算的评分项。")
    return {"earned": known if complete else None, "known_score": known, "complete": complete, "items": items, "warnings": warnings}
