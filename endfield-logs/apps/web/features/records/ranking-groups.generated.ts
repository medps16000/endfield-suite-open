// 本文件由 infra/scripts/dungeon_catalog_tool.py generate 自动生成，请勿手改。
// 数据源: data/local_tables/dungeon_catalog.json（新版本副本请改 JSON 后重新生成）。

export type RankingGroupKey = "crisis" | "crisisFragment" | "contingencyContract" | "warEcho" | "shadowPhase1" | "shadowPhase2" | "shadowPhase3" | "shadowPhase4" | "shadowPhase5" | "shadowPhase6";

export type RankingBossEntry = {
  slug: string;
  name: string;
  dungeonName: string;
};

export type RankingGroup = {
  key: RankingGroupKey;
  label: string;
  note: string;
  bosses: RankingBossEntry[];
};

export const rankingGroups: RankingGroup[] = [
  {
    key: "crisis",
    label: "危境再现",
    note: "收录 7 个危境再现首领",
    bosses: [
      { slug: "dung01_group_bossrush01", name: "危境再现·罗丹", dungeonName: "危境再现" },
      { slug: "dung01_group_bossrush02", name: "危境再现·三位一体", dungeonName: "危境再现" },
      { slug: "dung01_group_bossrush03", name: "危境再现·白垩界卫", dungeonName: "危境再现" },
      { slug: "dung02_group_bossrush01", name: "危境再现·阮一", dungeonName: "危境再现" },
      { slug: "dung02_group_bossrush02", name: "危境再现·聂菲斯", dungeonName: "危境再现" },
      { slug: "dung02_group_bossrush03", name: "危境再现·阿莱克琉斯", dungeonName: "危境再现" },
      { slug: "dung02_group_bossrush04", name: "危境再现·祀", dungeonName: "危境再现" },
    ],
  },
  {
    key: "crisisFragment",
    label: "危境碎片",
    note: "巨山犼兽 / 蚀影噪雷 / 幽林之怒",
    bosses: [
      { slug: "dung02_group_minibossrush01", name: "巨山犼兽", dungeonName: "危境碎片" },
      { slug: "dung02_group_minibossrush02", name: "蚀影噪雷", dungeonName: "危境碎片" },
      { slug: "dung02_group_minibossrush03", name: "幽林之怒", dungeonName: "危境碎片" },
    ],
  },
  {
    key: "contingencyContract",
    label: "危机合约",
    note: "活动榜单",
    bosses: [
      { slug: "indie_group_ccdg", name: "危机合约", dungeonName: "危机合约" },
    ],
  },
  {
    key: "warEcho",
    label: "战争回响",
    note: "收录全部 20 关最高难度（残酷）",
    bosses: [
      { slug: "indie_battletower001_ex", name: "白刃穿水·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower002_ex", name: "野性旧事·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower003_ex", name: "弓弩表象·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower004_ex", name: "斧柄纪年·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower005_ex", name: "铳弹砺石·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower006_ex", name: "裂地旧创·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower007_ex", name: "死兽鸣吼·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower008_ex", name: "战争简史·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower009_ex", name: "掩埋阵线·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower010_ex", name: "方阵庇护·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower011_ex", name: "重伤之围·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower012_ex", name: "无机狂热·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower013_ex", name: "斩首蓄势·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower014_ex", name: "野兽诡计·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower015_ex", name: "蚀影记录：雷声·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower016_ex", name: "长刃记录：应龙·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower017_ex", name: "复仇记录：碾骨·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower018_ex", name: "兽群记录：清波寨·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower019_ex", name: "造物记录：傀·残酷", dungeonName: "战争回响" },
      { slug: "indie_battletower020_ex", name: "防卫记录：盾片·残酷", dungeonName: "战争回响" },
    ],
  },
  {
    key: "shadowPhase1",
    label: "影拓丰碑1期",
    note: "收录 9 个苦难副本",
    bosses: [
      { slug: "indie_hard008_s", name: "怨憎雾海·苦难", dungeonName: "影拓丰碑1期 · 灼痛疤痕" },
      { slug: "indie_hard009_s", name: "血肉熔点·苦难", dungeonName: "影拓丰碑1期 · 灼痛疤痕" },
      { slug: "indie_hard007_s", name: "呼吼炽焰·苦难", dungeonName: "影拓丰碑1期 · 灼痛疤痕" },
      { slug: "indie_hard002_s", name: "矢影环伺·苦难", dungeonName: "影拓丰碑1期 · 无机造物" },
      { slug: "indie_hard001_s", name: "狂冲盾止·苦难", dungeonName: "影拓丰碑1期 · 无机造物" },
      { slug: "indie_hard003_s", name: "安于磐石·苦难", dungeonName: "影拓丰碑1期 · 无机造物" },
      { slug: "indie_hard006_s", name: "毒雾求生·苦难", dungeonName: "影拓丰碑1期 · 大地的弃子" },
      { slug: "indie_hard005_s", name: "旁门外道·苦难", dungeonName: "影拓丰碑1期 · 大地的弃子" },
      { slug: "indie_hard004_s", name: "弩斧相应·苦难", dungeonName: "影拓丰碑1期 · 大地的弃子" },
    ],
  },
  {
    key: "shadowPhase2",
    label: "影拓丰碑2期",
    note: "浊流具现 · 6 个苦难副本",
    bosses: [
      { slug: "indie_hard013_s", name: "沉寂视界·苦难", dungeonName: "影拓丰碑2期 · 浊流具现" },
      { slug: "indie_hard014_s", name: "枯竭海退·苦难", dungeonName: "影拓丰碑2期 · 浊流具现" },
      { slug: "indie_hard010_s", name: "潮涌遗恨·苦难", dungeonName: "影拓丰碑2期 · 浊流具现" },
      { slug: "indie_hard012_s", name: "污浊血脉·苦难", dungeonName: "影拓丰碑2期 · 浊流具现" },
      { slug: "indie_hard015_s", name: "潜流绝窟·苦难", dungeonName: "影拓丰碑2期 · 浊流具现" },
      { slug: "indie_hard011_s", name: "霜冻联结·苦难", dungeonName: "影拓丰碑2期 · 浊流具现" },
    ],
  },
  {
    key: "shadowPhase3",
    label: "影拓丰碑3期",
    note: "死寂争鸣 · 6 个苦难副本",
    bosses: [
      { slug: "indie_hard016_s", name: "仪式旋流·苦难", dungeonName: "影拓丰碑3期 · 死寂争鸣" },
      { slug: "indie_hard017_s", name: "死寂表象·苦难", dungeonName: "影拓丰碑3期 · 死寂争鸣" },
      { slug: "indie_hard018_s", name: "忿鼓咆声·苦难", dungeonName: "影拓丰碑3期 · 死寂争鸣" },
      { slug: "indie_hard019_s", name: "刺痛盾阵·苦难", dungeonName: "影拓丰碑3期 · 死寂争鸣" },
      { slug: "indie_hard020_s", name: "溶解窥看·苦难", dungeonName: "影拓丰碑3期 · 死寂争鸣" },
      { slug: "indie_hard021_s", name: "冯河断水·苦难", dungeonName: "影拓丰碑3期 · 死寂争鸣" },
    ],
  },
  {
    key: "shadowPhase4",
    label: "影拓丰碑4期",
    note: "山中见犼 · 4 个苦难副本",
    bosses: [
      { slug: "indie_hard022_s", name: "撼山雾火·苦难", dungeonName: "影拓丰碑4期 · 山中见犼" },
      { slug: "indie_hard023_s", name: "兽群把戏·苦难", dungeonName: "影拓丰碑4期 · 山中见犼" },
      { slug: "indie_hard024_s", name: "山犼争王·苦难", dungeonName: "影拓丰碑4期 · 山中见犼" },
      { slug: "indie_hard025_s", name: "清波访客·苦难", dungeonName: "影拓丰碑4期 · 山中见犼" },
    ],
  },
  {
    key: "shadowPhase5",
    label: "影拓丰碑5期",
    note: "幽影刻形 · 4 个苦难副本",
    bosses: [
      { slug: "indie_hard026_s", name: "兽影生杀·苦难", dungeonName: "影拓丰碑5期 · 幽影刻形" },
      { slug: "indie_hard027_s", name: "暗曜白霆·苦难", dungeonName: "影拓丰碑5期 · 幽影刻形" },
      { slug: "indie_hard028_s", name: "沛然轰鸣·苦难", dungeonName: "影拓丰碑5期 · 幽影刻形" },
      { slug: "indie_hard029_s", name: "三角定位·苦难", dungeonName: "影拓丰碑5期 · 幽影刻形" },
    ],
  },
  {
    key: "shadowPhase6",
    label: "影拓丰碑6期",
    note: "深林呓语 · 4 个苦难副本",
    bosses: [
      { slug: "indie_hard030_s", name: "受缚之影·苦难", dungeonName: "影拓丰碑6期 · 深林呓语" },
      { slug: "indie_hard031_s", name: "相引之骸·苦难", dungeonName: "影拓丰碑6期 · 深林呓语" },
      { slug: "indie_hard032_s", name: "怨怒延展·苦难", dungeonName: "影拓丰碑6期 · 深林呓语" },
      { slug: "indie_hard033_s", name: "虫傀呼应·苦难", dungeonName: "影拓丰碑6期 · 深林呓语" },
    ],
  },
];
