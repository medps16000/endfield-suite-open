export const ROTATION_IMPORT_MAX_BYTES = 8 * 1024 * 1024;
export type RotationActionKind = "battle_skill" | "combo_skill" | "ultimate_skill";
export type RotationAction = {
  kind: RotationActionKind;
  name: string;
  skillName: string;
  iconUrl: string;
  rootSkillIds: string[];
};

export type RotationStep = {
  id: string;
  atMs: number;
  characterId: string;
  skillId?: string;
  actionKind?: RotationActionKind;
  note: string;
  phase: string;
};

export type RotationDraft = {
  name: string;
  dungeonId: string;
  characterIds: string[];
  enabled: boolean;
  steps: RotationStep[];
  displayNotes: string;
};

export type RotationPlan = RotationDraft & {
  id: string;
  position: number;
  revision: number;
  updatedAt: string;
};

export type RotationEnvelope = {
  schemaVersion: 1 | 2;
  accountId: string;
  revision: string;
  rotations: RotationPlan[];
};

export type RotationCatalog = {
  schemaVersion: 2;
  maps: { id: string; name: string; seriesName: string }[];
  characters: {
    id: string;
    name: string;
    avatarUrl: string;
    skills: { id: string; name: string; groupId: string }[];
    actions: RotationAction[];
  }[];
};

export class RotationApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code?: string) {
    super(message);
    this.name = "RotationApiError";
  }
}

export async function rotationRequest<T>(path: string, init?: RequestInit, accountId?: string): Promise<T> {
  const headers = new Headers(init?.headers);
  if (accountId) headers.set("X-Endfield-Rotation-Account", accountId);
  if (init?.body) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    // Both the production API and the scoped Next preview proxy share this origin.
    response = await fetch(`/api/rotations${path}`, {
      ...init, headers, credentials: "include", cache: "no-store",
    });
  } catch (cause) {
    if (init?.signal?.aborted) throw cause;
    throw new RotationApiError("无法连接排轴服务，请检查网络后重试。", 0);
  }
  let body: unknown;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) {
    const error = body as { error?: { code?: string; message?: string }; detail?: unknown } | null;
    const fallback = response.status === 401 ? "登录已失效，请重新登录后继续。"
      : response.status === 409 ? "这套排轴已在其他页面修改，请重新载入后再保存。"
      : response.status === 422 ? "排轴内容不符合要求，请检查地图、四人阵容和步骤。"
      : response.status === 403 ? "当前账号无法操作这套排轴。"
      : "排轴服务暂时不可用，请稍后重试。";
    throw new RotationApiError(error?.error?.message || (typeof error?.detail === "string" ? error.detail : fallback), response.status, error?.error?.code);
  }
  if (body === null) throw new RotationApiError("排轴服务返回了无法读取的内容，请稍后重试。", 502);
  return body as T;
}

export function rotationBody(plan: RotationDraft): RotationDraft {
  return {
    name: plan.name, dungeonId: plan.dungeonId, characterIds: [...plan.characterIds],
    enabled: plan.enabled, displayNotes: plan.displayNotes,
    steps: plan.steps.map(({ id, atMs, characterId, skillId, actionKind, note, phase }) => ({ id, atMs, characterId,
      ...(skillId !== undefined ? { skillId } : {}), ...(actionKind !== undefined ? { actionKind } : {}), note, phase })),
  };
}

export function serializeRotationExport(plan: RotationDraft): string {
  return JSON.stringify({ format: "zmdlogs.rotation.v2", schemaVersion: 2, rotation: rotationBody(plan) }, null, 2);
}

export function parseRotationImport(text: string): RotationDraft {
  if (new TextEncoder().encode(text).length > ROTATION_IMPORT_MAX_BYTES) throw new Error("排轴文件不能超过 8 MiB。");
  let data: unknown;
  try { data = JSON.parse(text); } catch { throw new Error("文件不是有效的 JSON 排轴。"); }
  const envelope = data as { format?: unknown; schemaVersion?: unknown; rotation?: unknown } | null;
  if (!envelope || !((envelope.format === "zmdlogs.rotation.v1" && envelope.schemaVersion === 1)
    || (envelope.format === "zmdlogs.rotation.v2" && envelope.schemaVersion === 2))) {
    throw new Error("请导入本站导出的 ZMDLogs 排轴文件。");
  }
  const plan = envelope.rotation as Record<string, unknown> | null;
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) throw new Error("文件缺少排轴内容。");
  const isText = (value: unknown, limit: number) => typeof value === "string" && value.length <= limit;
  if (!isText(plan.name, 80) || !(plan.name as string).trim() || !isText(plan.dungeonId, 160)
    || !Array.isArray(plan.characterIds) || plan.characterIds.length !== 4
    || plan.characterIds.some((id) => !isText(id, 160)) || new Set(plan.characterIds).size !== 4
    || typeof plan.enabled !== "boolean" || !isText(plan.displayNotes, 2000)
    || !Array.isArray(plan.steps) || plan.steps.length < 1 || plan.steps.length > 500) {
    throw new Error("文件中的名称、地图、四人阵容或步骤格式不正确。");
  }
  const steps: RotationStep[] = [];
  const ids = new Set<string>();
  for (const raw of plan.steps) {
    const step = raw as Record<string, unknown> | null;
    if (!step || typeof step !== "object" || Array.isArray(step) || !isText(step.id, 80) || !(step.id as string).trim()
      || !Number.isInteger(step.atMs) || (step.atMs as number) < 0 || (step.atMs as number) > 3_600_000
      || !isText(step.characterId, 160) || !isText(step.note, 500) || !isText(step.phase, 80)
      || ids.has(step.id as string)) {
      throw new Error("文件中有格式错误或重复的步骤，时间范围应为 0～3600 秒。");
    }
    const exactSkill = isText(step.skillId, 160) && (step.skillId as string).length > 0 && step.actionKind === undefined;
    const action = envelope.schemaVersion === 2 && step.skillId === undefined
      && ["battle_skill", "combo_skill", "ultimate_skill"].includes(step.actionKind as string);
    if (!exactSkill && !action) throw new Error("步骤需要选择一个有效动作，不能同时填写不同的技能选择。");
    ids.add(step.id as string);
    if (steps.length && (step.atMs as number) < steps[steps.length - 1].atMs) throw new Error("步骤时间必须按顺序递增，相同时间可以并列。");
    steps.push({ id: step.id as string, atMs: step.atMs as number, characterId: step.characterId as string,
      ...(exactSkill ? { skillId: step.skillId as string } : { actionKind: step.actionKind as RotationActionKind }),
      note: step.note as string, phase: step.phase as string });
  }
  // Owner, server revision, activation order and timestamps are never imported.
  return rotationBody({ name: plan.name as string, dungeonId: plan.dungeonId as string,
    characterIds: plan.characterIds as string[], enabled: false, displayNotes: plan.displayNotes as string, steps });
}
