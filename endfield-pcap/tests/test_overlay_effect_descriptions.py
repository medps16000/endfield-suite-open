from __future__ import annotations

from endfield_pcap.overlay_effect_descriptions import describe_official_buff


def test_actual_atk_config_is_explained_without_using_dynamic_fallback_as_current_value():
    root = {"id": "unrelated_id", "attributeModifier": {"isConvertedAttribute": False,
            "attributeModifiers": [{"attributeType": "Atk", "modifyAttributeType": "Specific",
            "formulaItem": "BaseMultiplier", "param": {"useBlackboardKey": True, "blackboardKey": "atk_up", "value": 999}}]},
            "duration": {"useBlackboardKey": True, "blackboardKey": "atk_duration", "value": 777},
            "stackingSettings": {"stackingType": "EnhanceAndRefresh", "useMaxStackCntKey": False, "maxStackCnt": 2}}
    description = describe_official_buff(root)
    assert description["label"] == "攻击力修饰"
    assert "基础值乘算" in description["description"]
    assert "atk_up" in description["description"] and "atk_duration" in description["description"]
    assert "999" not in description["description"] and "777" not in description["description"]
    assert "不代表当前层数" in description["description"]
    assert description["labelSource"] == description["descriptionSource"] == "official-config-summary"


def test_template_name_and_inactive_default_attribute_do_not_create_a_name():
    assert describe_official_buff({"id": "buff_character_atkup_fire_vulnerable"}) == {}
    result = describe_official_buff({"attributeModifier": {"attributeModifiers": [
        {"attributeType": "Atk", "modifyAttributeType": "SomeOtherMode"}]}})
    assert result["label"] is None


def test_event_explanation_does_not_claim_observed_trigger_or_flatten_conditions():
    result = describe_official_buff({"buffEventAction": [{"buffEvent": "OnOutputDamage", "actions": [{}]}]})
    assert result["label"] is None
    assert "造成伤害时" in result["description"] and "不代表这些条件本场已触发" in result["description"]
    assert describe_official_buff({"buffEventAction": [{"buffEvent": "OnOutputDamage", "actions": []}]}) == {}


def test_dynamic_maximum_and_unlimited_mode_are_not_shown_as_observed_stacks():
    for mode, dynamic in (("Unlimited", False), ("EnhanceAndRefresh", True)):
        result = describe_official_buff({"stackingSettings": {"stackingType": mode,
            "useMaxStackCntKey": dynamic, "maxStackCnt": 44}})
        assert result == {}


def test_ability_event_summary_preserves_literal_enum_and_conditional_sequence():
    root = {"id": "buff_Ether_Cure", "abilityEventAction": [{"abilityEvent": "OnTeleportationPointRest",
        "actions": [{"actionData": [
            {"$type": "CheckMainCharacterCondition.Data", "isEnable": True},
            {"$type": "CreateGlobalBuffAction.Data", "isEnable": True,
             "globalBuffId": {"id": "global_buff_ether_cure_buff"}, "value": 999},
        ]}]}]}
    result = describe_official_buff(root)
    assert result["label"] is None
    assert "包含能力事件配置：OnTeleportationPointRest" in result["description"]
    assert "条件和动作顺序未展平" in result["description"]
    assert "不代表本场已触发或子效果已生效" in result["description"]
    assert "999" not in result["description"] and "治疗" not in result["description"]
    assert result["descriptionFields"] == ["abilityEventAction[0]"]
    for actions in ([], None, "not a sequence"):
        root["abilityEventAction"][0]["actions"] = actions
        assert describe_official_buff(root) == {}


def test_skill_button_progress_is_explicit_configuration_not_a_missing_icon_guess():
    root = {"hasIcon": True, "iconConfig": {"_spritePath": "", "showProgressInNormalSkillButton": True}}
    result = describe_official_buff(root)
    assert result["label"] == "技能按钮进度"
    assert "未提供独立图标" in result["description"]
    assert "不代表已观测到当前进度" in result["description"]
    for changed in ({"hasIcon": False}, {"iconConfig": {"_spritePath": "other", "showProgressInNormalSkillButton": True}},
                    {"iconConfig": {"_spritePath": "", "showProgressInNormalSkillButton": 1}}):
        assert describe_official_buff({**root, **changed}) == {}
    root["attributeModifier"] = {"attributeModifiers": [{"modifyAttributeType": "Specific", "attributeType": "Atk"}]}
    assert describe_official_buff(root)["label"] == "攻击力修饰"
