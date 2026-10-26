import Link from "next/link";
import type { Metadata } from "next";
import { getCurrentUser } from "../../lib/api/server";
import { RotationEditor } from "../../components/rotation-editor";
import { RotationsUnavailable } from "../../components/rotations-unavailable";
import { ROTATIONS_ENABLED } from "../../lib/feature-flags";
import "./rotations.css";

export const dynamic = "force-dynamic";
export const metadata: Metadata = ROTATIONS_ENABLED
  ? { title: "排轴工作台 · 终末地战斗日志", description: "编排战斗技能，保存到账号并同步客户端指导条。" }
  : { title: "排轴功能暂时停用 · 终末地战斗日志", description: "排轴编辑与指导功能目前暂停使用。" };

export default async function RotationsPage() {
  if (!ROTATIONS_ENABLED) return <RotationsUnavailable />;
  const user = await getCurrentUser();
  return (
    <div className="rotation-page">
      <header className="rotation-page-heading">
        <span className="eyebrow">ROTATION WORKSHOP</span>
        <h1>排轴工作台<span aria-hidden="true">.</span></h1>
        <p>编排技能顺序，在战斗开始时显示你的指导条。</p>
      </header>
      {user ? <RotationEditor key={user.id} accountId={user.id} /> : <section className="rotation-panel rotation-login">
        <h2>登录后开始编排</h2>
        <p>排轴保存在你的账号中，客户端同步后可离线使用。</p>
        <Link className="button-primary" href="/login">前往登录</Link>
      </section>}
    </div>
  );
}
