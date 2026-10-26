"use client";

import { useEffect, useRef, type ReactNode } from "react";
import type { RotationAction, RotationCatalog, RotationDraft, RotationStep } from "../lib/api/rotations";
import "./rotation-guide-preview.css";

type Character = RotationCatalog["characters"][number];
type Presentation = { action?: RotationAction; name: string; kindName: string };

type RotationGuidePreviewProps = {
  draft: RotationDraft;
  catalog: RotationCatalog | null;
  selectedStepId: string | null;
  onSelectStep: (stepId: string) => void;
  presentStep: (step: RotationStep, character: Character | undefined) => Presentation;
  renderArtwork: (action: RotationAction | undefined, avatarUrl: string | undefined, size: number) => ReactNode;
};

function previewTime(atMs: number) {
  if (!Number.isInteger(atMs) || atMs < 0 || atMs > 3_600_000) return "时间待填";
  return `${Math.floor(atMs / 60_000)}:${((atMs % 60_000) / 1000).toFixed(3).padStart(6, "0")}`;
}

export function RotationGuidePreview({ draft, catalog, selectedStepId, onSelectStep, presentStep, renderArtwork }: RotationGuidePreviewProps) {
  const timeline = useRef<HTMLOListElement>(null);
  const selectedIndex = draft.steps.findIndex((step) => step.id === selectedStepId);
  const currentIndex = selectedIndex < 0 ? 0 : selectedIndex;
  const current = draft.steps[currentIndex];
  const visible = draft.steps.slice(currentIndex, currentIndex + 4);
  const characters = new Map(catalog?.characters.map((character) => [character.id, character]));
  const map = catalog?.maps.find((entry) => entry.id === draft.dungeonId);

  useEffect(() => {
    const container = timeline.current;
    const button = container?.querySelector<HTMLElement>('[aria-current="step"]');
    if (!container || !button) return;
    const bounds = container.getBoundingClientRect();
    const selectedBounds = button.getBoundingClientRect();
    if (selectedBounds.left < bounds.left || selectedBounds.right > bounds.right) {
      container.scrollLeft += selectedBounds.left - bounds.left - (bounds.width - selectedBounds.width) / 2;
    }
  }, [current?.id, currentIndex]);

  return <section className="rotation-panel rotation-guide-preview" aria-label="编辑指导条预览">
    <div className="rotation-section-heading rgp-heading">
      <div><span className="eyebrow">GUIDE PREVIEW</span><h2>编辑预览</h2></div>
      <span className="rotation-help">当前一步 · 后续三步</span>
    </div>
    <p className="rgp-explanation">按选中步骤预览计划时间，战斗中由客户端按游戏计时提示。</p>
    <div className="rgp-stage">
      <div className="rgp-plan"><strong>{draft.name || "未命名排轴"}</strong><span>{map?.name || "尚未选择地图"}</span></div>
      {current ? <>
        <div className="rgp-phase"><span>{current.phase || "当前步骤"}</span><span>计划时间</span></div>
        <ol className="rgp-window" aria-label="当前步骤与后续三步">
          {visible.map((step, offset) => {
            const character = characters.get(step.characterId);
            const presentation = presentStep(step, character);
            return <li key={step.id} className={offset === 0 ? "rgp-current" : "rgp-next"}>
              <button type="button" onClick={() => onSelectStep(step.id)}
                aria-label={`${offset === 0 ? "当前预览" : `后续第 ${offset} 步`}：第 ${currentIndex + offset + 1} 步，${character?.name || "选择角色"}，${presentation.name}，计划时间 ${previewTime(step.atMs)}`}
                aria-current={offset === 0 ? "step" : undefined}>
                <span className="rgp-step-kind">{offset === 0 ? "当前" : `后续 ${offset}`}<small>{presentation.kindName}</small></span>
                <span className="rgp-artwork">{renderArtwork(presentation.action, character?.avatarUrl, offset === 0 ? 72 : 52)}</span>
                <strong className="rgp-character">{character?.name || "选择角色"}</strong>
                <span className="rgp-skill">{presentation.name}</span>
                <time>{previewTime(step.atMs)}</time>
              </button>
            </li>;
          })}
          {Array.from({ length: 4 - visible.length }, (_, offset) => <li className="rgp-remaining" key={`remaining-${offset}`} aria-hidden="true"><span>—</span></li>)}
        </ol>
        {current.note ? <div className="rgp-note"><span>操作备注</span><p>{current.note}</p></div> : null}
        {currentIndex === draft.steps.length - 1 && <p className="rgp-last-step">已到排轴最后一步</p>}
      </> : <div className="rgp-empty"><strong>还没有技能步骤</strong><p>添加步骤后，可在这里查看当前动作与后续三个提示。</p></div>}
    </div>
    {draft.steps.length > 0 && <>
      <div className="rgp-navigation">
        <span>全部步骤 <strong>{currentIndex + 1}</strong> / {draft.steps.length}</span>
        <div><button type="button" disabled={currentIndex === 0} onClick={() => onSelectStep(draft.steps[currentIndex - 1].id)} aria-label="预览上一步">←</button>
          <button type="button" disabled={currentIndex === draft.steps.length - 1} onClick={() => onSelectStep(draft.steps[currentIndex + 1].id)} aria-label="预览下一步">→</button></div>
      </div>
      <ol className="rgp-timeline" aria-label="选择预览步骤" ref={timeline}>
        {draft.steps.map((step, index) => {
          const character = characters.get(step.characterId);
          const presentation = presentStep(step, character);
          return <li key={step.id}>
            <button type="button" onClick={() => onSelectStep(step.id)} aria-current={index === currentIndex ? "step" : undefined}
              aria-label={`预览第 ${index + 1} 步：${character?.name || "选择角色"}，${presentation.name}，计划时间 ${previewTime(step.atMs)}${step.phase ? `，${step.phase}` : ""}${step.note ? `，${step.note}` : ""}`}>
              <span className="rgp-timeline-number">{String(index + 1).padStart(2, "0")}</span>
              {renderArtwork(presentation.action, character?.avatarUrl, 34)}
              <span className="rgp-timeline-kind">{presentation.kindName}</span>
              <time>{previewTime(step.atMs)}</time>
            </button>
          </li>;
        })}
      </ol>
    </>}
  </section>;
}
