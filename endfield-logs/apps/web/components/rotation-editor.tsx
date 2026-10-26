"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RotationGuidePreview } from "./rotation-guide-preview";
import { parseRotationImport, ROTATION_IMPORT_MAX_BYTES, RotationApiError, rotationBody, rotationRequest, serializeRotationExport,
  type RotationAction, type RotationCatalog, type RotationDraft, type RotationEnvelope, type RotationPlan, type RotationStep } from "../lib/api/rotations";

const EMPTY_DRAFT: RotationDraft = { name: "", dungeonId: "", characterIds: [], enabled: false, steps: [], displayNotes: "" };
type ValidationField = "name" | "dungeonId" | "characterIds" | "displayNotes" | "steps"
  | "stepIdentity" | "stepTime" | "stepCharacter" | "stepAction" | "stepNote" | "stepPhase";
type ValidationIssue = { code: string; field: ValidationField; message: string; stepIndex?: number; stepId?: string };

function copyDraft(plan: RotationDraft): RotationDraft { return rotationBody(plan); }
function seconds(ms: number) { return Number.isFinite(ms) ? (ms / 1000).toFixed(3).replace(/\.?0+$/, "") || "0" : ""; }
function timeLabel(ms: number) { return Number.isFinite(ms) ? `${Math.floor(ms / 60000)}:${((ms % 60000) / 1000).toFixed(3).padStart(6, "0")}` : "时间待填"; }
function stepPresentation(step: RotationStep, character: RotationCatalog["characters"][number] | undefined) {
  const candidates = character?.actions?.filter((entry) => step.skillId === undefined
    ? entry.kind === step.actionKind
    : step.actionKind === undefined && entry.rootSkillIds.includes(step.skillId));
  const action = candidates?.length === 1 ? candidates[0] : undefined;
  const exact = step.actionKind === undefined ? character?.skills.find((entry) => entry.id === step.skillId) : undefined;
  return { action, name: exact?.name || action?.skillName || action?.name || "选择动作",
    kindName: action?.name || (exact ? "原有技能" : "待选动作") };
}
function ActionArtwork({ action, avatarUrl, size = 48 }: { action?: RotationAction; avatarUrl?: string; size?: number }) {
  return <span className="rotation-action-art" style={{ width: size, height: size }} aria-hidden="true">
    {action?.iconUrl ? <Image className="rotation-action-icon" src={action.iconUrl} width={size} height={size} alt="" />
      : <span className="rotation-action-fallback">{action?.name.slice(0, 1) || "技"}</span>}
    {avatarUrl && <Image className="rotation-action-avatar" src={avatarUrl} width={22} height={22} alt="" unoptimized={!avatarUrl.startsWith("/images/")} />}
  </span>;
}
function StepTimeInput({ atMs, onChange, invalid, stepId, rawTexts }: { atMs: number; onChange: (atMs: number) => void; invalid?: boolean;
  stepId: string; rawTexts: Map<string, string> }) {
  const [text, setText] = useState(rawTexts.get(stepId) ?? seconds(atMs));
  const lastEmitted = useRef(atMs);
  useEffect(() => { if (!Object.is(atMs, lastEmitted.current)) { const next = seconds(atMs); rawTexts.set(stepId, next); setText(next); lastEmitted.current = atMs; } }, [atMs, stepId, rawTexts]);
  return <input data-rotation-field="stepTime" aria-invalid={invalid || undefined} type="text" inputMode="decimal" maxLength={12} value={text} placeholder="0.000" onChange={(event) => {
    const next = event.target.value;
    rawTexts.set(stepId, next);
    setText(next);
    const parsed = /^\d+(?:\.\d{0,3})?$/.test(next) ? Math.round(Number(next) * 1000) : Number.NaN;
    lastEmitted.current = parsed; onChange(parsed);
  }} />;
}
function ordered(plans: RotationPlan[]) { return [...plans].sort((a, b) => a.position - b.position || a.id.localeCompare(b.id)); }
function checkOwner(envelope: RotationEnvelope, accountId: string) {
  if (envelope.accountId !== accountId) throw new RotationApiError("登录账号已经变更，请重新打开工作台。", 401);
}
function validationIssues(draft: RotationDraft, catalog: RotationCatalog): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const add = (code: string, field: ValidationField, message: string, stepIndex?: number) => issues.push({ code, field, message,
    ...(stepIndex === undefined ? {} : { stepIndex, stepId: draft.steps[stepIndex].id }) });
  const characters = new Map(catalog.characters.map((character) => [character.id, character]));
  if (!draft.name.trim()) add("name_required", "name", "先为这套排轴起个名字。");
  if (draft.name.length > 80) add("name_length", "name", "排轴名称最多 80 字。");
  if (draft.displayNotes.length > 2000) add("notes_length", "displayNotes", "适用说明最多 2000 字。");
  if (!catalog.maps.some((map) => map.id === draft.dungeonId)) add("map_unavailable", "dungeonId", "请选择网站榜单收录的地图与难度。");
  if (draft.characterIds.length !== 4 || new Set(draft.characterIds).size !== 4
    || draft.characterIds.some((id) => !characters.has(id))) add("squad_invalid", "characterIds", "请选择四名不同角色。");
  if (!draft.steps.length || draft.steps.length > 500) add("step_count", "steps", "排轴需要 1～500 个步骤。");
  const ids = new Set<string>();
  for (let index = 0; index < draft.steps.length; index += 1) {
    const step = draft.steps[index];
    const character = characters.get(step.characterId);
    if (!step.id.trim() || step.id.length > 80 || ids.has(step.id)) add("step_identity", "stepIdentity", `第 ${index + 1} 步的标识无效或重复，请删除该步后重新添加。`, index);
    ids.add(step.id);
    const validTime = Number.isInteger(step.atMs) && step.atMs >= 0 && step.atMs <= 3_600_000;
    if (!validTime) add("step_time", "stepTime", `第 ${index + 1} 步的时间应为 0～3600 秒，最多保留 3 位小数。`, index);
    if (validTime && index && Number.isInteger(draft.steps[index - 1].atMs) && step.atMs < draft.steps[index - 1].atMs)
      add("step_order", "stepTime", `第 ${index + 1} 步的时间早于上一步，请调整时间或步骤顺序。`, index);
    const exact = step.actionKind === undefined && !!step.skillId && character?.skills.some((skill) => skill.id === step.skillId);
    const action = step.skillId === undefined && character?.actions?.some((entry) => entry.kind === step.actionKind && entry.rootSkillIds.length > 0);
    if (!character || !draft.characterIds.includes(step.characterId))
      add("step_character", "stepCharacter", `第 ${index + 1} 步需要选择阵容中的角色及对应动作。`, index);
    else if (!exact && !action) add("step_action", "stepAction", `第 ${index + 1} 步需要选择阵容中的角色及对应动作。`, index);
    if (step.note.length > 500) add("step_note", "stepNote", `第 ${index + 1} 步的操作备注最多 500 字。`, index);
    if (step.phase.length > 80) add("step_phase", "stepPhase", `第 ${index + 1} 步的轴段名称最多 80 字。`, index);
  }
  return issues;
}
function validationError(draft: RotationDraft, catalog: RotationCatalog): string | null { return validationIssues(draft, catalog)[0]?.message || null; }

export function RotationEditor({ accountId }: { accountId: string }) {
  const [catalog, setCatalog] = useState<RotationCatalog | null>(null);
  const [plans, setPlans] = useState<RotationPlan[]>([]);
  const [selected, setSelected] = useState<RotationPlan | null>(null);
  const [draft, setDraft] = useState<RotationDraft>(copyDraft(EMPTY_DRAFT));
  const [savedDraft, setSavedDraft] = useState(JSON.stringify(copyDraft(EMPTY_DRAFT)));
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [unauthorized, setUnauthorized] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [characterSearch, setCharacterSearch] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importError, setImportError] = useState("");
  const [previewStep, setPreviewStep] = useState<string | null>(null);
  const [validationFocus, setValidationFocus] = useState<ValidationIssue | null>(null);
  const [showAllIssues, setShowAllIssues] = useState(false);
  const [shareText, setShareText] = useState("");
  const [shareHelp, setShareHelp] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const stepList = useRef<HTMLOListElement>(null);
  const workshop = useRef<HTMLDivElement>(null);
  const importDialog = useRef<HTMLElement>(null);
  const sharePanel = useRef<HTMLElement>(null);
  const shareFallback = useRef<HTMLTextAreaElement>(null);
  const stepTimeTexts = useRef(new Map<string, string>());
  const shareAttempt = useRef(0);
  const shareContext = useRef({ draft, accountId, unauthorized });
  shareContext.current = { draft, accountId, unauthorized };
  const loadGeneration = useRef(0);
  const dirty = JSON.stringify(draft) !== savedDraft;

  useEffect(() => {
    const header = document.querySelector<HTMLElement>(".site-header");
    if (!header) return;
    const measure = () => workshop.current?.style.setProperty("--rotation-header-height", `${header.getBoundingClientRect().height}px`);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(header);
    return () => observer.disconnect();
  }, []);

  const openDraft = useCallback((plan: RotationPlan | null, body?: RotationDraft) => {
    const next = copyDraft(body || plan || EMPTY_DRAFT);
    stepTimeTexts.current.clear();
    shareAttempt.current += 1;
    setShareText(""); setShareHelp("");
    setSelected(plan); setDraft(next); setSavedDraft(JSON.stringify(copyDraft(plan || EMPTY_DRAFT)));
    setPreviewStep(null); setCharacterSearch(""); setConflict(false); setError("");
    setValidationFocus(null); setShowAllIssues(false);
  }, []);

  const reportError = useCallback((cause: unknown) => {
    const accountChanged = cause instanceof RotationApiError && cause.code === "ROTATION_ACCOUNT_CHANGED";
    if (cause instanceof RotationApiError && (cause.status === 401 || accountChanged)) {
      setPlans([]); openDraft(null); setImportOpen(false); setImportText(""); setImportError(""); setNotice("");
    }
    setError(cause instanceof Error ? cause.message : "操作没有完成，请稍后重试。");
    if (cause instanceof RotationApiError) {
      setUnauthorized(cause.status === 401 || accountChanged); setConflict(cause.status === 409 && !accountChanged);
    }
  }, [openDraft]);

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++loadGeneration.current;
    Promise.all([rotationRequest<RotationCatalog>("/catalog", { signal: controller.signal }), rotationRequest<RotationEnvelope>("", { signal: controller.signal }, accountId)])
      .then(([nextCatalog, envelope]) => {
        if (generation !== loadGeneration.current) return;
        checkOwner(envelope, accountId);
        setCatalog(nextCatalog); setPlans(ordered(envelope.rotations));
        const first = ordered(envelope.rotations)[0];
        if (first) openDraft(first);
      }).catch((cause: unknown) => { if (!controller.signal.aborted) reportError(cause); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); loadGeneration.current += 1; };
  }, [accountId, openDraft, reportError]);

  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    const handleNavigation = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
        || !(event.target instanceof Element)) return;
      const link = event.target.closest("a[href]");
      if (!(link instanceof HTMLAnchorElement) || link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
      const destination = new URL(link.href, window.location.href);
      const current = new URL(window.location.href);
      if (destination.origin !== current.origin || (destination.pathname === current.pathname && destination.search === current.search)) return;
      if (!window.confirm("有尚未保存的修改。要放弃修改并离开排轴工作台吗？")) {
        event.preventDefault(); event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", handler);
    document.addEventListener("click", handleNavigation, true);
    return () => { window.removeEventListener("beforeunload", handler); document.removeEventListener("click", handleNavigation, true); };
  }, [dirty]);

  useEffect(() => {
    if (!importOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImportOpen(false);
      if (event.key !== "Tab") return;
      const focusable = importDialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), textarea, input:not([type=file]), a[href]");
      if (!focusable?.length) return;
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === importDialog.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    importDialog.current?.focus(); document.addEventListener("keydown", handler);
    return () => { document.removeEventListener("keydown", handler); previous?.focus(); };
  }, [importOpen]);

  const mapsBySeries = useMemo(() => {
    const groups = new Map<string, RotationCatalog["maps"]>();
    for (const map of catalog?.maps || []) {
      const series = map.seriesName || "其他地图";
      groups.set(series, [...(groups.get(series) || []), map]);
    }
    return [...groups.entries()];
  }, [catalog]);
  const squad = draft.characterIds.map((id) => catalog?.characters.find((entry) => entry.id === id))
    .filter((entry): entry is RotationCatalog["characters"][number] => !!entry);
  const shownCharacters = catalog?.characters.filter((entry) => entry.name.includes(characterSearch.trim())) || [];
  const currentMap = catalog?.maps.find((entry) => entry.id === draft.dungeonId);
  const mapUnavailable = Boolean(catalog && draft.dungeonId && !currentMap);
  const currentStep = draft.steps.find((step) => step.id === previewStep) || draft.steps[0];
  const currentCharacter = catalog?.characters.find((entry) => entry.id === currentStep?.characterId);
  const currentIndex = draft.steps.findIndex((step) => step.id === currentStep?.id);
  const currentPresentation = currentStep ? stepPresentation(currentStep, currentCharacter) : null;
  const issues = useMemo(() => catalog ? validationIssues(draft, catalog) : [], [draft, catalog]);

  useEffect(() => {
    shareAttempt.current += 1;
    setShareText(""); setShareHelp("");
    return () => { shareAttempt.current += 1; };
  }, [draft, accountId]);

  useEffect(() => {
    if (!validationFocus) return;
    if (validationFocus.stepId !== undefined && (currentStep?.id !== validationFocus.stepId
      || (validationFocus.field === "stepIdentity" && draft.steps[validationFocus.stepIndex!]?.id !== validationFocus.stepId))) {
      setValidationFocus(null); return;
    }
    const selector = validationFocus.field === "stepIdentity"
      ? `[data-step-index="${validationFocus.stepIndex}"] .rotation-step-select`
      : `[data-rotation-field="${validationFocus.field}"]`;
    const target = workshop.current?.querySelector<HTMLElement>(selector);
    target?.scrollIntoView({ block: "center" });
    target?.focus({ preventScroll: true });
    setValidationFocus(null);
  }, [validationFocus, currentStep?.id, draft.steps]);

  useEffect(() => {
    if (!shareText) return;
    sharePanel.current?.scrollIntoView({ block: "center" });
    shareFallback.current?.focus({ preventScroll: true });
    shareFallback.current?.select();
  }, [shareText]);

  function focusIssue(issue: ValidationIssue) {
    if (issue.stepIndex !== undefined) {
      let index = issue.stepIndex;
      const matches = draft.steps.flatMap((step, position) => step.id === issue.stepId ? [position] : []);
      if (issue.field !== "stepIdentity" && matches.length !== 1) return;
      if (draft.steps[index]?.id !== issue.stepId) {
        if (matches.length !== 1) return;
        index = matches[0];
      }
      const step = draft.steps[index];
      if (!step) return;
      setPreviewStep(step.id);
      setValidationFocus({ ...issue, stepIndex: index });
      return;
    }
    setValidationFocus(issue);
  }

  useEffect(() => {
    if (!previewStep || !stepList.current) return;
    const list = stepList.current;
    const selectedStep = list.querySelector<HTMLElement>(".rotation-step-select.is-selected");
    if (!selectedStep) return;
    const bounds = list.getBoundingClientRect();
    const selectedBounds = selectedStep.getBoundingClientRect();
    if (selectedBounds.top < bounds.top) list.scrollTop -= bounds.top - selectedBounds.top;
    else if (selectedBounds.bottom > bounds.bottom) list.scrollTop += selectedBounds.bottom - bounds.bottom;
  }, [previewStep, currentIndex]);

  function confirmDiscard() { return !dirty || window.confirm("有尚未保存的修改。要放弃这些修改吗？"); }
  async function reload() {
    if (!confirmDiscard()) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const [nextCatalog, envelope] = await Promise.all([rotationRequest<RotationCatalog>("/catalog"), rotationRequest<RotationEnvelope>("", undefined, accountId)]);
      checkOwner(envelope, accountId);
      const nextPlans = ordered(envelope.rotations);
      setCatalog(nextCatalog); setPlans(nextPlans); setUnauthorized(false);
      openDraft(nextPlans.find((plan) => plan.id === selected?.id) || nextPlans[0] || null);
      setNotice("已载入账号中保存的最新排轴。");
    } catch (cause) { reportError(cause); }
    finally { setLoading(false); setBusy(false); }
  }
  function choose(plan: RotationPlan) {
    if (!confirmDiscard()) return;
    setNotice(""); openDraft(plan);
  }
  function newPlan() {
    if (unauthorized) return;
    if (!confirmDiscard()) return;
    setNotice(""); openDraft(null);
  }
  function duplicate() {
    if (!catalog) return;
    setNotice("副本尚未保存，保存后会出现在列表末尾。"); setError("");
    let name = "";
    for (const character of draft.name || "未命名排轴") {
      if (name.length + character.length > 75) break;
      name += character;
    }
    const next = { ...copyDraft(draft), name: `${name} · 副本`, enabled: false,
      steps: draft.steps.map((step) => ({ ...step, id: crypto.randomUUID() })) };
    openDraft(null, next);
  }
  async function save() {
    if (!catalog) return;
    const issue = validationError(draft, catalog);
    if (issue) { setError(issue); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const body = rotationBody({ ...draft, name: draft.name.trim() });
      const plan = await rotationRequest<RotationPlan>(selected ? `/${selected.id}` : "", { method: selected ? "PATCH" : "POST",
        body: JSON.stringify(selected ? { ...body, revision: selected.revision } : body) }, accountId);
      setPlans((previous) => ordered([...previous.filter((entry) => entry.id !== plan.id), plan]));
      openDraft(plan); setNotice("已保存。客户端同步后，这套排轴也可离线使用。");
    } catch (cause) { reportError(cause); }
    finally { setBusy(false); }
  }
  async function toggle(plan: RotationPlan) {
    if (plan.id === selected?.id && !confirmDiscard()) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await rotationRequest<RotationPlan>(`/${plan.id}`, { method: "PATCH", body: JSON.stringify({ ...rotationBody(plan), enabled: !plan.enabled, revision: plan.revision }) }, accountId);
      setPlans((previous) => ordered(previous.map((entry) => entry.id === updated.id ? updated : entry)));
      if (selected?.id === plan.id) openDraft(updated);
      setNotice(updated.enabled ? "已激活，匹配时按列表顺序选择。" : "已停用，客户端不会自动触发这套排轴。");
    } catch (cause) { reportError(cause); }
    finally { setBusy(false); }
  }
  async function movePlan(index: number, direction: number) {
    const destination = index + direction;
    if (destination < 0 || destination >= plans.length) return;
    const next = [...plans]; [next[index], next[destination]] = [next[destination], next[index]];
    setBusy(true); setError(""); setNotice("");
    try {
      const envelope = await rotationRequest<RotationEnvelope>("/order", { method: "POST", body: JSON.stringify({ ids: next.map((plan) => plan.id) }) }, accountId);
      checkOwner(envelope, accountId);
      const nextPlans = ordered(envelope.rotations); setPlans(nextPlans);
      const replacement = nextPlans.find((plan) => plan.id === selected?.id);
      if (replacement && !dirty) openDraft(replacement);
      else if (replacement && JSON.stringify(copyDraft(replacement)) === savedDraft) {
        // A metadata-only reorder may advance the revision. The complete
        // returned content must match the editor's original server baseline.
        setSelected(replacement); setConflict(false);
      } else if (replacement) {
        setConflict(true); setError("排轴内容已在其他页面修改。当前草稿已保留，请先导出草稿，再重新载入核对。");
      }
      setNotice("已更新顺序。客户端自动使用已激活且匹配的第一套。");
    } catch (cause) { reportError(cause); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!selected || !window.confirm(`删除「${selected.name}」？客户端下一次同步后也会移除这套排轴。`)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await rotationRequest<{ ok: boolean }>(`/${selected.id}`, { method: "DELETE" }, accountId);
      const next = plans.filter((plan) => plan.id !== selected.id); setPlans(next); openDraft(next[0] || null);
      setNotice("排轴已删除。");
      // Deletion compacts positions and advances the remaining plan revisions.
      // Reload those revisions before allowing another edit from this list.
      try {
        const envelope = await rotationRequest<RotationEnvelope>("", undefined, accountId);
        checkOwner(envelope, accountId);
        const current = ordered(envelope.rotations); setPlans(current); openDraft(current[0] || null);
      } catch (cause) {
        reportError(cause);
        if (!(cause instanceof RotationApiError && (cause.status === 401 || cause.code === "ROTATION_ACCOUNT_CHANGED"))) setError("排轴已删除，但列表刷新失败。请重新载入后继续编辑。");
      }
    } catch (cause) { reportError(cause); }
    finally { setBusy(false); }
  }
  function toggleCharacter(id: string) {
    if (draft.characterIds.includes(id)) {
      if (draft.steps.some((step) => step.characterId === id) && !window.confirm("移除此角色后，相关步骤会保留，请重新选择角色和技能。继续吗？")) return;
      setDraft((previous) => ({ ...previous, characterIds: previous.characterIds.filter((entry) => entry !== id) }));
    } else if (draft.characterIds.length < 4) setDraft((previous) => ({ ...previous, characterIds: [...previous.characterIds, id] }));
  }
  function updateStep(id: string, changes: Partial<RotationStep>) {
    setDraft((previous) => ({ ...previous, steps: previous.steps.map((step) => step.id === id ? { ...step, ...changes } : step) }));
  }
  function addStep(characterId?: string, actionKind?: RotationStep["actionKind"], duplicateCurrent = false) {
    if (busy || !catalog || unauthorized || draft.characterIds.length !== 4 || draft.steps.length >= 500) return;
    if (duplicateCurrent && !currentStep) return;
    if ((characterId !== undefined || actionKind !== undefined) && (!characterId || !draft.characterIds.includes(characterId)
      || !catalog.characters.find((entry) => entry.id === characterId)?.actions?.some((action) => action.kind === actionKind && action.rootSkillIds.length > 0))) return;
    const step: RotationStep = duplicateCurrent && currentStep ? { ...currentStep, id: crypto.randomUUID() }
      : { id: crypto.randomUUID(), atMs: currentStep?.atMs ?? 0,
        characterId: characterId ?? currentStep?.characterId ?? "", phase: currentStep?.phase || "", note: "",
        ...(actionKind ? { actionKind } : {}) };
    const position = currentIndex < 0 ? draft.steps.length : currentIndex + 1;
    setDraft((previous) => ({ ...previous, steps: [...previous.steps.slice(0, position), step, ...previous.steps.slice(position)] }));
    setPreviewStep(step.id);
  }
  function moveStep(index: number, direction: number) {
    const destination = index + direction;
    if (destination < 0 || destination >= draft.steps.length) return;
    const next = [...draft.steps]; [next[index], next[destination]] = [next[destination], next[index]];
    setDraft((previous) => ({ ...previous, steps: next }));
  }
  function exportPlan() {
    if (!catalog) return;
    const issue = validationError(draft, catalog);
    if (issue) { setError(issue); return; }
    const url = URL.createObjectURL(new Blob([serializeRotationExport(draft)], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url;
    anchor.download = `${draft.name.trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, "_") || "rotation"}.json`;
    anchor.click(); URL.revokeObjectURL(url); setNotice("已导出当前编辑内容。导入的排轴默认停用，确认后再激活。");
  }
  async function copyShareContent() {
    if (busy || !catalog || unauthorized) return;
    const issue = validationIssues(draft, catalog)[0];
    if (issue) { setError(issue.message); focusIssue(issue); return; }
    const content = serializeRotationExport(draft);
    const attempt = ++shareAttempt.current;
    const isCurrent = () => attempt === shareAttempt.current && shareContext.current.draft === draft
      && shareContext.current.accountId === accountId && !shareContext.current.unauthorized;
    setShareText(""); setShareHelp("");
    try {
      if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(content);
      if (isCurrent()) setShareHelp("已复制分享内容。对方可在本站的「导入」中粘贴，检查后保存。");
    } catch {
      if (!isCurrent()) return;
      setShareText(content);
      setShareHelp("浏览器未允许自动复制。可选中下方内容后手动复制，或点击「导出」发送 JSON 文件。");
    }
  }
  function importPlan() {
    if (!catalog || unauthorized) return;
    try {
      const next = parseRotationImport(importText);
      const issue = validationError(next, catalog);
      if (issue) throw new Error(issue);
      if (!confirmDiscard()) return;
      openDraft(null, next); setImportOpen(false); setImportText(""); setImportError("");
      setNotice("已导入为未保存排轴，默认停用。确认内容后点击保存。");
    } catch (cause) { setImportError(cause instanceof Error ? cause.message : "无法导入这份排轴。"); }
  }

  return <div className="rotation-content" ref={workshop}>
    <div className="rotation-workshop-note">
      <span className="rotation-note-icon" aria-hidden="true">↳</span>
      <p>只匹配地图与四人阵容，站位顺序不限。官方计时开始后触发，收到对应技能释放后标记完成；状态无法确认时不显示。</p>
    </div>
    <div className="rotation-feedback" aria-live="polite">
      {error && <div className="rotation-message is-error" role="alert"><span>{error}</span>
        {unauthorized ? <a href="/rotations">重新打开工作台</a> : <button type="button" onClick={reload} disabled={busy}>{conflict ? "重新载入" : "重试载入"}</button>}
      </div>}
      {notice && <div className="rotation-message"><span>{notice}</span><button type="button" onClick={() => setNotice("")} aria-label="关闭提示">×</button></div>}
    </div>
    {loading ? <section className="rotation-panel rotation-loading" aria-busy="true">正在载入你的排轴…</section> :
      <div className="rotation-workspace">
        <aside className="rotation-panel rotation-library">
          <div className="rotation-section-heading"><h2>我的排轴 <small>{plans.length}</small></h2>
            <button className="rotation-icon-button" type="button" onClick={reload} disabled={busy} aria-label="重新载入账号排轴">↻</button>
          </div>
          <div className="rotation-library-actions">
            <button className="button-primary" type="button" onClick={newPlan} disabled={busy || !catalog || unauthorized}>＋ 新建</button>
            <button className="button-secondary" type="button" onClick={() => { setImportOpen(true); setImportError(""); }} disabled={busy || !catalog || unauthorized}>导入</button>
          </div>
          <p className="rotation-help">顺序即触发优先级，自动取已激活且匹配的第一套。</p>
          {!plans.length && <p className="rotation-empty">还没有排轴。新建一套，为地图选择四人阵容。</p>}
          <ol className="rotation-plan-list">
            {plans.map((plan, index) => <li key={plan.id} className={`rotation-plan-card${selected?.id === plan.id ? " is-selected" : ""}`}>
              <button className="rotation-plan-select" type="button" onClick={() => choose(plan)} disabled={busy}>
                <span className="rotation-plan-position">{String(index + 1).padStart(2, "0")}</span>
                <span className="rotation-plan-meta"><strong>{plan.name}</strong><small>{catalog?.maps.find((map) => map.id === plan.dungeonId)?.name || "地图数据待载入"}</small>
                  <span className="rotation-mini-squad">{plan.characterIds.map((id) => {
                    const character = catalog?.characters.find((entry) => entry.id === id);
                    return character?.avatarUrl ? <Image src={character.avatarUrl} width={24} height={24} alt={character.name} key={id} unoptimized={!character.avatarUrl.startsWith("/images/")} /> : <span key={id}>{character?.name || "角色待更新"}</span>;
                  })}</span>
                </span>
                <span className={`rotation-status${plan.enabled ? " is-enabled" : ""}`}>{plan.enabled ? "已激活" : "停用"}</span>
              </button>
              <div className="rotation-plan-controls">
                <button type="button" onClick={() => toggle(plan)} disabled={busy || unauthorized}>{plan.enabled ? "停用" : "激活"}</button>
                <div><button type="button" onClick={() => movePlan(index, -1)} disabled={busy || index === 0 || unauthorized} aria-label={`${plan.name}上移`}>↑</button>
                  <button type="button" onClick={() => movePlan(index, 1)} disabled={busy || index === plans.length - 1 || unauthorized} aria-label={`${plan.name}下移`}>↓</button></div>
              </div>
            </li>)}
          </ol>
          <p className="rotation-cache-note">登录同一账号的客户端会缓存已保存排轴。离线时使用最近一次同步成功的版本。</p>
        </aside>
        <div className="rotation-editor-main">
          <section className="rotation-panel">
            <div className="rotation-section-heading"><div><span className="eyebrow">PLAN SETTINGS</span><h2>{selected ? "编辑排轴" : "新建排轴"}</h2></div>
              <span className={`rotation-save-state${dirty ? " is-dirty" : ""}`}>{dirty ? "尚未保存" : selected ? "已保存" : "未创建"}</span>
            </div>
            <fieldset disabled={busy || !catalog || unauthorized} className="rotation-fieldset">
              <div className="rotation-form-grid">
                <label className="rotation-field">排轴名称<input data-rotation-field="name" value={draft.name} maxLength={80} placeholder="例如：三角定位 · 双终结技轴" onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
                <label className="rotation-field"><span>地图 <span className="rotation-help">· 仅限网站榜单</span></span><select data-rotation-field="dungeonId" aria-label="地图" value={draft.dungeonId} aria-invalid={mapUnavailable || undefined} onChange={(event) => setDraft({ ...draft, dungeonId: event.target.value })}>
                  <option value="">选择榜单地图与难度</option>
                  {mapUnavailable && <option value={draft.dungeonId} disabled>原地图未收录榜单，请重新选择</option>}
                  {mapsBySeries.map(([series, maps]) => <optgroup label={series} key={series}>{maps.map((map) => <option key={map.id} value={map.id}>{map.name}</option>)}</optgroup>)}
                </select></label>
              </div>
              {mapUnavailable && <p className="rotation-inline-error">原地图不在当前榜单收录范围内，请重新选择后保存。</p>}
              <div className="rotation-squad-heading"><label className="rotation-field-label">四人阵容 <span>{draft.characterIds.length}/4</span></label>
                <input aria-label="搜索阵容角色" placeholder="搜索角色" value={characterSearch} onChange={(event) => setCharacterSearch(event.target.value)} />
              </div>
              <div className="rotation-character-picker" data-rotation-field="characterIds" role="group" aria-label="四人阵容" tabIndex={-1}>
                {shownCharacters.map((character) => <button key={character.id} type="button" aria-pressed={draft.characterIds.includes(character.id)}
                  className={`rotation-character${draft.characterIds.includes(character.id) ? " is-selected" : ""}`}
                  disabled={!draft.characterIds.includes(character.id) && draft.characterIds.length >= 4} onClick={() => toggleCharacter(character.id)}>
                  {character.avatarUrl && <Image src={character.avatarUrl} width={30} height={30} alt="" unoptimized={!character.avatarUrl.startsWith("/images/")} />}
                  <span>{character.name}</span>{draft.characterIds.includes(character.id) && <span className="rotation-character-check" aria-hidden="true">✓</span>}
                </button>)}
              </div>
              {!shownCharacters.length && <p className="rotation-help">没有找到对应角色。</p>}
              <label className="rotation-field rotation-display-notes">适用说明 <span className="rotation-help">装备、潜能和精炼仅展示，不参与匹配</span>
                <textarea data-rotation-field="displayNotes" rows={3} maxLength={2000} value={draft.displayNotes} placeholder="写下武器、装备、潜能要求，以及操作时需要注意的事项。" onChange={(event) => setDraft({ ...draft, displayNotes: event.target.value })} />
              </label>
              <label className="rotation-enabled-option"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>激活这套排轴<small>保存并同步后，匹配地图和阵容时可自动触发。</small></span></label>
            </fieldset>
          </section>
          <section className={`rotation-panel rotation-validation${catalog && !issues.length ? " is-valid" : ""}`} aria-labelledby="rotation-validation-title">
            <div className="rotation-section-heading"><h2 id="rotation-validation-title">检查排轴</h2>
              <span className="rotation-validation-summary" role="status" aria-live="polite" aria-atomic="true">{!catalog ? "资料未载入，暂不能检查" : issues.length ? `待处理 ${issues.length} 项` : "检查通过 · 无待处理问题"}</span>
            </div>
            {issues.length > 0 ? <>
              <p className="rotation-help">点击问题，直接定位到需要调整的位置。</p>
              <ul className="rotation-issue-list">{issues.slice(0, showAllIssues ? issues.length : 8).map((issue) => <li key={`${issue.code}-${issue.stepIndex ?? "plan"}`}>
                <button type="button" onClick={() => focusIssue(issue)}><span>{issue.message}</span><small>定位 →</small></button>
              </li>)}</ul>
              {issues.length > 8 && <button className="rotation-issue-toggle" type="button" onClick={() => setShowAllIssues((previous) => !previous)}>{showAllIssues ? "收起问题" : `查看全部 ${issues.length} 项`}</button>}
            </> : catalog && <p className="rotation-help">地图、四人阵容、动作与时间顺序均已检查，可以保存或分享。</p>}
          </section>
          <section className="rotation-panel" data-rotation-field="steps" tabIndex={-1}>
            <div className="rotation-section-heading"><div><span className="eyebrow">ACTION TIMELINE</span><h2>技能时间轴 <small>{draft.steps.length} 步</small></h2></div>
              <button className="button-secondary" type="button" onClick={() => addStep()} disabled={busy || !catalog || unauthorized || draft.steps.length >= 500 || draft.characterIds.length !== 4}>＋ 空白步骤</button>
            </div>
            <p className="rotation-help">点技能添加一步；选中列表里的步骤，在右侧调整时间、角色和动作。同一时间可安排多个技能。</p>
            <fieldset className="rotation-quick-add" disabled={busy || !catalog || unauthorized || draft.steps.length >= 500 || draft.characterIds.length !== 4}>
              <legend>快捷添加技能</legend>
              <p className="rotation-quick-context">{currentStep ? `插入第 ${currentIndex + 1} 步后 · 沿用 ${timeLabel(currentStep.atMs)}${currentStep.phase ? ` · ${currentStep.phase}` : ""}` : "选择四人阵容后，点技能添加第一步"}</p>
              <div className="rotation-quick-roster">{squad.map((character) => <div className="rotation-quick-row" key={character.id}>
                <span className="rotation-quick-character">{character.avatarUrl && <Image src={character.avatarUrl} width={30} height={30} alt="" unoptimized={!character.avatarUrl.startsWith("/images/")} />}<strong>{character.name}</strong></span>
                {character.actions?.map((action) => <button className="rotation-quick-action" type="button" key={action.kind}
                  onClick={() => addStep(character.id, action.kind)} aria-label={`添加${character.name}的${action.name}`} title={`${character.name} · ${action.skillName}`}>
                  <ActionArtwork action={action} size={30} /><span>{action.name}</span><span className="rotation-quick-plus" aria-hidden="true">＋</span>
                </button>)}
                {!character.actions?.length && <span className="rotation-quick-unavailable">技能资料待更新</span>}
              </div>)}</div>
            </fieldset>
            {!draft.steps.length && <div className="rotation-timeline-empty">选择四人阵容后，添加第一个技能步骤。</div>}
            <div className="rotation-step-workbench">
            <ol className="rotation-step-list" aria-label="排轴步骤" ref={stepList}>
              {draft.steps.map((step, index) => {
                const character = catalog?.characters.find((entry) => entry.id === step.characterId);
                const presentation = stepPresentation(step, character);
                return <li key={step.id} data-step-index={index}>
                  {step.phase && (index === 0 || step.phase !== draft.steps[index - 1].phase) && <div className="rotation-step-phase">{step.phase}</div>}
                  <button type="button" className={`rotation-step-select${currentStep?.id === step.id ? " is-selected" : ""}`}
                  onClick={() => setPreviewStep(step.id)} aria-pressed={currentStep?.id === step.id}
                  aria-label={`编辑第 ${index + 1} 步：${character?.name || "待选角色"}，${presentation.name}，${seconds(step.atMs)} 秒`}>
                  <span className="rotation-step-number">{String(index + 1).padStart(2, "0")}</span>
                  <ActionArtwork action={presentation.action} avatarUrl={character?.avatarUrl} size={36} />
                  <span className="rotation-step-summary"><strong>{character?.name || "选择角色"}<small>{presentation.kindName}</small></strong><span>{presentation.name}</span>
                    {step.note && <span className="rotation-step-note" title={step.note}>{step.note}</span>}</span>
                  <time>{seconds(step.atMs)}<small>秒</small></time>
                </button></li>;
              })}
            </ol>
            {currentStep && <fieldset className="rotation-step-detail" disabled={busy || !catalog || unauthorized} key={currentStep.id}>
              <div className="rotation-step-detail-heading"><h3>步骤 {String(currentIndex + 1).padStart(2, "0")}</h3>
                <div className="rotation-step-controls"><button className="rotation-copy-step" type="button" onClick={() => addStep(undefined, undefined, true)} disabled={draft.steps.length >= 500 || draft.characterIds.length !== 4} aria-label={`复制第 ${currentIndex + 1} 步`}>复制此步</button>
                  <button type="button" onClick={() => moveStep(currentIndex, -1)} disabled={currentIndex === 0} aria-label={`第 ${currentIndex + 1} 步上移`}>↑</button>
                  <button type="button" onClick={() => moveStep(currentIndex, 1)} disabled={currentIndex === draft.steps.length - 1} aria-label={`第 ${currentIndex + 1} 步下移`}>↓</button>
                  <button className="rotation-remove-step" type="button" onClick={() => {
                    setDraft({ ...draft, steps: draft.steps.filter((entry) => entry.id !== currentStep.id) });
                    setPreviewStep(draft.steps[currentIndex - 1]?.id || draft.steps[currentIndex + 1]?.id || null);
                  }} aria-label={`删除第 ${currentIndex + 1} 步`}>×</button></div>
              </div>
              <label className="rotation-field rotation-time-field">副本计时（秒）<StepTimeInput stepId={currentStep.id} rawTexts={stepTimeTexts.current} atMs={currentStep.atMs} invalid={issues.some((issue) => issue.field === "stepTime" && issue.stepIndex === currentIndex)} onChange={(atMs) => updateStep(currentStep.id, { atMs })} /></label>
              <div className="rotation-field"><span>选择角色</span><div className="rotation-step-characters" data-rotation-field="stepCharacter" tabIndex={-1} role="group" aria-label="步骤角色">
                {squad.map((entry) => <button key={entry.id} type="button" aria-pressed={currentStep.characterId === entry.id}
                  className={currentStep.characterId === entry.id ? "is-selected" : ""} onClick={() => {
                    if (currentStep.characterId !== entry.id) updateStep(currentStep.id, { characterId: entry.id, skillId: undefined, actionKind: undefined });
                  }}>
                  {entry.avatarUrl && <Image src={entry.avatarUrl} width={38} height={38} alt="" unoptimized={!entry.avatarUrl.startsWith("/images/")} />}<span>{entry.name}</span>
                </button>)}
              </div></div>
              {currentStep.characterId && !draft.characterIds.includes(currentStep.characterId) && <p className="rotation-inline-error">原角色已移出阵容，请重新选择。</p>}
              <div className="rotation-field"><span>选择动作</span><div className="rotation-action-picker" data-rotation-field="stepAction" tabIndex={-1} role="group" aria-label="步骤动作">
                {currentCharacter?.actions?.map((action) => <button type="button" key={action.kind} className={currentStep.actionKind === action.kind ? "is-selected" : ""}
                  aria-pressed={currentStep.actionKind === action.kind} disabled={!draft.characterIds.includes(currentStep.characterId)}
                  onClick={() => updateStep(currentStep.id, { actionKind: action.kind, skillId: undefined })}>
                  <ActionArtwork action={action} size={40} /><strong>{action.name}</strong><small>{action.skillName}</small>
                </button>)}
              </div>
              {!currentCharacter && <p className="rotation-help">先选择这一步使用的角色。</p>}
              {currentCharacter && !currentCharacter.actions?.length && <p className="rotation-help">该角色的动作资料暂不可用，请更新资料后再编排。</p>}
              {currentStep.skillId && <p className="rotation-legacy-choice">原有技能：<strong>{currentPresentation?.name}</strong>。可保留，也可重新选择上方动作。</p>}
              </div>
              <label className="rotation-field">操作备注<input data-rotation-field="stepNote" maxLength={500} value={currentStep.note} placeholder="例如：重击后释放 / 切到前台" onChange={(event) => updateStep(currentStep.id, { note: event.target.value })} /></label>
              <label className="rotation-field">轴段 <span className="rotation-help">选填</span><input data-rotation-field="stepPhase" maxLength={80} value={currentStep.phase} placeholder="例如：开场 / 第二轮" onChange={(event) => updateStep(currentStep.id, { phase: event.target.value })} /></label>
            </fieldset>}
            </div>
            {draft.steps.length > 0 && <button className="rotation-add-bottom" type="button" onClick={() => addStep()} disabled={busy || unauthorized || draft.steps.length >= 500 || draft.characterIds.length !== 4}>＋ 在选中步骤后添加空白步骤</button>}
          </section>
          <RotationGuidePreview draft={draft} catalog={catalog} selectedStepId={currentStep?.id ?? null} onSelectStep={setPreviewStep}
            presentStep={stepPresentation} renderArtwork={(action, avatarUrl, size) => <ActionArtwork action={action} avatarUrl={avatarUrl} size={size} />} />
          {shareHelp && <section className="rotation-panel rotation-share" ref={sharePanel} aria-labelledby="rotation-share-title">
            <div className="rotation-section-heading"><h2 id="rotation-share-title">分享排轴</h2><button className="rotation-icon-button" type="button" onClick={() => { setShareText(""); setShareHelp(""); }} aria-label="关闭分享提示">×</button></div>
            <p className="rotation-share-help" role="status">{shareHelp}</p>
            {shareText && <>
              <label className="rotation-field">排轴分享内容<textarea ref={shareFallback} readOnly rows={7} spellCheck={false} value={shareText} /></label>
              <button className="button-secondary" type="button" onClick={() => { shareFallback.current?.focus(); shareFallback.current?.select(); }}>选中分享内容</button>
              <p className="rotation-help">电脑上按 Ctrl+C，手机上长按后选择复制。内容与本站导出的 JSON 文件相同。</p>
            </>}
          </section>}
          <footer className="rotation-save-bar">
            <span>{selected ? `保存版本 ${selected.revision}` : "新排轴默认放在列表末尾"}{dirty ? " · 尚未保存" : ""}</span>
            <div><button className="button-secondary" type="button" onClick={duplicate} disabled={busy || !catalog || unauthorized}>复制排轴</button>
              <button className="button-secondary" type="button" onClick={copyShareContent} disabled={busy || !catalog || unauthorized || issues.length > 0}>复制分享内容</button>
              <button className="button-secondary" type="button" onClick={exportPlan} disabled={busy || !catalog}>导出</button>
              {selected && <button className="rotation-delete-plan" type="button" onClick={remove} disabled={busy || unauthorized}>删除</button>}
              <button className="button-primary" type="button" onClick={save} disabled={busy || !catalog || unauthorized || conflict}>{busy ? "处理中…" : "保存排轴"}</button></div>
          </footer>
        </div>
      </div>}
    {importOpen && <div className="rotation-dialog-backdrop" onClick={() => setImportOpen(false)}><section ref={importDialog} tabIndex={-1} className="rotation-dialog rotation-panel" role="dialog" aria-modal="true" aria-labelledby="rotation-import-title" onClick={(event) => event.stopPropagation()}>
      <div className="rotation-section-heading"><h2 id="rotation-import-title">导入排轴</h2><button className="rotation-icon-button" type="button" onClick={() => setImportOpen(false)} aria-label="关闭导入">×</button></div>
      <p className="rotation-help">选择本站导出的 JSON 文件，或粘贴文件内容，最多 8 MiB。导入后先检查，再保存和激活。</p>
      <input ref={fileInput} type="file" accept=".json,application/json" className="rotation-file-input" onChange={async (event) => {
        const file = event.target.files?.[0]; if (!file) return;
        if (file.size > ROTATION_IMPORT_MAX_BYTES) { setImportError("排轴文件不能超过 8 MiB。"); return; }
        try { setImportText(await file.text()); setImportError(""); } catch { setImportError("无法读取这份文件，请重新选择。"); }
        event.target.value = "";
      }} />
      <button className="button-secondary" type="button" onClick={() => fileInput.current?.click()}>选择 JSON 文件</button>
      <label className="rotation-field rotation-import-text">文件内容<textarea value={importText} rows={10} maxLength={ROTATION_IMPORT_MAX_BYTES} spellCheck={false} onChange={(event) => {
        const next = event.target.value;
        if (new TextEncoder().encode(next).length > ROTATION_IMPORT_MAX_BYTES) { setImportError("排轴文件不能超过 8 MiB。"); return; }
        setImportText(next); setImportError("");
      }} /></label>
      {importError && <p className="rotation-inline-error" role="alert">{importError}</p>}
      <div className="rotation-dialog-actions"><button className="button-secondary" type="button" onClick={() => setImportOpen(false)}>取消</button><button className="button-primary" type="button" onClick={importPlan} disabled={!importText.trim()}>导入为新排轴</button></div>
    </section></div>}
  </div>;
}
