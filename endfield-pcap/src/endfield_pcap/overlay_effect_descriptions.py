"""Display-only explanations of exact official Buff configuration fields.

These are configuration summaries, not official Buff names, runtime values or
new gameplay rules. No template-name parsing or parent-skill guessing occurs.
"""
from __future__ import annotations

ATTRIBUTE_LABELS = {
    "Atk": "攻击力", "MaxHp": "生命上限", "Def": "防御力", "Str": "力量",
    "Agi": "敏捷", "Wisd": "智识", "Will": "意志", "CriticalRate": "暴击率",
    "CriticalDamageIncrease": "暴击伤害加成", "PhysicalDamageIncrease": "物理伤害加成",
    "FireDamageIncrease": "灼热伤害加成", "PulseDamageIncrease": "电磁伤害加成",
    "CrystDamageIncrease": "寒冷伤害加成", "NaturalDamageIncrease": "自然伤害加成",
    "TurnRateScalar": "转向速率系数", "SlowActionSpeedScalar": "动作减速系数",
    "UltimateSpGainScalar": "终结技能量获取系数",
}
FORMULA_LABELS = {
    "BaseAddition": "基础值加算", "BaseMultiplier": "基础值乘算",
    "BaseFinalMultiplier": "基础值最终乘算", "Multiplier": "乘算", "Addition": "加算",
    "FinalMultiplier": "最终乘算", "BaseFinalAddition": "基础值最终加算", "FinalAddition": "最终加算",
}
EVENT_LABELS = {
    "DuringBuffEnable": "启用期间", "OnBuffEnable": "启用时", "OnBuffStart": "开始时",
    "OnBuffTrigger": "触发时", "OnBuffFinish": "结束时", "OnBuffReleased": "释放时",
    "OnBeforeTakeDamage": "受到伤害前", "OnTakeDamage": "受到伤害时",
    "OnBeforeCastSkill": "施放技能前", "OnBeforeOutputDamage": "造成伤害前",
    "OnOutputDamage": "造成伤害时", "OnOutputBuff": "施加效果时", "OnAddedBuff": "获得效果时",
    "OnSkillStart": "技能开始时", "OnSkillEnd": "技能结束时",
}


def describe_official_buff(root: dict) -> dict:
    parts: list[str] = []
    labels: list[str] = []
    fields: list[str] = []
    modifier = root.get("attributeModifier") or {}
    if modifier.get("isConvertedAttribute") is True:
        parts.append("包含属性转换配置；转换条件和数值不在这里简化。")
        labels.append("属性转换配置")
        fields.append("attributeModifier.isConvertedAttribute")
    else:
        for index, row in enumerate(modifier.get("attributeModifiers") or []):
            if not isinstance(row, dict):
                continue
            attribute = row.get("attributeType")
            if row.get("modifyAttributeType") != "Specific" or attribute not in ATTRIBUTE_LABELS:
                parts.append("包含属性修饰配置；具体属性未作中文简化。")
                continue
            title = ATTRIBUTE_LABELS[attribute]
            labels.append(title + "修饰")
            sentence = title + "修饰"
            formula = row.get("formulaItem")
            if formula in FORMULA_LABELS:
                sentence += "（" + FORMULA_LABELS[formula] + "）"
            parameter = row.get("param") or {}
            key = parameter.get("blackboardKey")
            if parameter.get("useBlackboardKey") is True and isinstance(key, str) and key:
                sentence += "；参数来自运行时变量“" + key[:80] + "”（当前值另行观测）"
            else:
                sentence += "；这里只说明配置，不把配置参数当成本场伤害加成"
            parts.append(sentence + "。")
            fields.append(f"attributeModifier.attributeModifiers[{index}]")
    for key, label in (("damageModifier", "伤害修饰"), ("healModifier", "治疗修饰"),
                       ("poiseModifier", "韧性伤害修饰"), ("shieldConfigs", "护盾配置"), ("globalModifier", "全局修饰")):
        if root.get(key):
            labels.append(label)
            parts.append("包含" + label + "；具体条件和数值未展平为单一加成。")
            fields.append(key)
    events = []
    for index, group in enumerate(root.get("buffEventAction") or []):
        if isinstance(group, dict) and group.get("actions") and group.get("buffEvent") in EVENT_LABELS:
            event = EVENT_LABELS[group["buffEvent"]]
            if event not in events:
                events.append(event)
            fields.append(f"buffEventAction[{index}]")
    if events:
        parts.append("配置含事件逻辑：" + "、".join(events[:5]) + "；不代表这些条件本场已触发。")
    ability_events = []
    for index, group in enumerate(root.get("abilityEventAction") or []):
        if not isinstance(group, dict) or not isinstance(group.get("actions"), list) or not group["actions"]:
            continue
        event = group.get("abilityEvent")
        if not isinstance(event, str) or not event:
            continue
        # Unknown event enums remain literal configuration labels. In
        # particular, do not invent a Chinese translation or infer an
        # unconditional active child Buff from a configured action sequence.
        event_label = EVENT_LABELS.get(event, event[:80])
        if event_label not in ability_events:
            ability_events.append(event_label)
        fields.append(f"abilityEventAction[{index}]")
    if ability_events:
        parts.append("包含能力事件配置：" + "、".join(ability_events[:5]) + "；条件和动作顺序未展平，不代表本场已触发或子效果已生效。")
    icon_config = root.get("iconConfig") or {}
    if (root.get("hasIcon") is True and icon_config.get("_spritePath") == ""
            and icon_config.get("showProgressInNormalSkillButton") is True):
        parts.append("游戏配置为技能按钮进度标记，未提供独立图标；不代表已观测到当前进度。")
        if not labels:
            labels.append("技能按钮进度")
        fields.extend(("hasIcon", "iconConfig._spritePath", "iconConfig.showProgressInNormalSkillButton"))
    duration = root.get("duration") or {}
    if duration.get("useBlackboardKey") is True and isinstance(duration.get("blackboardKey"), str) and duration["blackboardKey"]:
        parts.append("持续时间来自运行时变量“" + duration["blackboardKey"][:80] + "”；没有实际值时保持未知。")
        fields.append("duration.blackboardKey")
    stacking = root.get("stackingSettings") or {}
    maximum = stacking.get("maxStackCnt")
    if (stacking.get("stackingType") in {"Enhance", "EnhanceAndRefresh"}
            and stacking.get("useMaxStackCntKey") is False and type(maximum) is int and 0 < maximum <= 100000):
        parts.append(f"配置最大层数：{maximum}；不代表当前层数。")
        fields.append("stackingSettings.maxStackCnt")
    if not parts:
        return {}
    unique_labels = list(dict.fromkeys(labels))
    label = "／".join(unique_labels[:2]) if unique_labels else None
    return {"label": label, "labelSource": "official-config-summary" if label else None,
            "description": "\n".join(parts)[:800], "descriptionSource": "official-config-summary",
            "descriptionFields": list(dict.fromkeys(fields))[:24]}
