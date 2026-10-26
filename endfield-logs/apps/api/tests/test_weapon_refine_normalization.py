from app.schemas.public import BattleRosterEntryResponse, BattleRosterWeaponResponse, BattleRosterWeaponSkillResponse
from app.services.public_data import _normalize_roster_weapon_refine


def test_unknown_new_weapon_refine_is_not_guessed_from_skill_level() -> None:
    entry = BattleRosterEntryResponse(
        slot=1,
        characterName='提弗洛斯',
        accountDisplayName='test',
        weapon=BattleRosterWeaponResponse(
            weaponTemplate='wpn_funnel_0019',
            weaponName='寒夜幽影',
            weaponRefine=0,
            skills=[
                BattleRosterWeaponSkillResponse(skillKey='sk_wpn_funnel_0019', level=9, potentialLevel=5),
            ],
        ),
    )
    normalized = _normalize_roster_weapon_refine([entry])[0]
    assert normalized.weapon is not None
    assert normalized.weapon.weaponRefine == 6
