import { computeCriticalLuck, type CriticalHit } from "../../lib/analysis/critical-luck";

const worker = globalThis as unknown as {
  onmessage: ((event: MessageEvent<{ hits: CriticalHit[]; durationMs: number }>) => void) | null;
  postMessage: (value: unknown) => void;
};
worker.onmessage = ({ data }) => {
  try {
    worker.postMessage({ result: computeCriticalLuck(data.hits, data.durationMs) });
  } catch (error) {
    worker.postMessage({ error: error instanceof Error ? error.message : "暴击参数暂不可计算" });
  }
};
