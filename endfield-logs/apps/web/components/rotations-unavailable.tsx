import Link from "next/link";
import { StateBlock } from "./state-block";

export function RotationsUnavailable() {
  return (
    <div className="page-stack">
      <StateBlock
        title="排轴功能暂时停用"
        description="排轴编辑与指导功能目前暂停使用，已保存的排轴数据仍会保留。"
      />
      <div><Link className="button-secondary" href="/">返回首页</Link></div>
    </div>
  );
}
