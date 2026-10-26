import Link from "next/link";

import { BindingCodePanel } from "../../../features/auth/binding-code-panel";
import { getCurrentUser } from "../../../lib/api/server";

export const dynamic = "force-dynamic";

export default async function BindingPage() {
  const user = await getCurrentUser();
  if (!user) {
    return (
      <section className="panel binding-code-panel">
        <h1>机器人绑定</h1>
        <p className="muted">请先登录你要绑定的 ZMDLogs 网站账号。</p>
        <Link className="button-primary" href="/login">前往登录</Link>
      </section>
    );
  }
  return <BindingCodePanel nickname={user.nickname} />;
}
