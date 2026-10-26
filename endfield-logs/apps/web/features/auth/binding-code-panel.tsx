"use client";

import { useEffect, useRef, useState } from "react";

import { buildApiUrl } from "../../lib/api/client";

type BindingCode = { code: string; expiresAt: string };

export function BindingCodePanel({ nickname }: { nickname: string }) {
  const [binding, setBinding] = useState<BindingCode | null>(null);
  const [now, setNow] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const generating = useRef(false);
  const codeInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!binding) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [binding]);

  const remaining = binding ? Math.max(0, Math.ceil((Date.parse(binding.expiresAt) - now) / 1000)) : 0;
  const active = binding !== null && remaining > 0;
  const timeLabel = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;

  async function generate() {
    if (generating.current) return;
    generating.current = true;
    setBusy(true);
    // A request may rotate the code even if its response is lost in transit.
    setBinding(null);
    setError("");
    setMessage("");
    try {
      const response = await fetch(buildApiUrl("/api/auth/binding-code"), {
        method: "POST", credentials: "include", cache: "no-store",
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(response.status === 401 ? "登录已失效，请重新登录后生成。" : data.error?.message || "生成失败，请稍后重试。");
      }
      if (typeof data.code !== "string" || !Number.isFinite(Date.parse(data.expiresAt))) {
        throw new Error("生成失败，请重试获取新的绑定码。");
      }
      setNow(Date.now());
      setBinding(data as BindingCode);
      setMessage("绑定码已生成，有效期 10 分钟。此前的绑定码已失效。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "网络异常，请重试获取新的绑定码。");
    } finally {
      setBusy(false);
      generating.current = false;
    }
  }

  async function copyCommand() {
    if (!binding || Date.parse(binding.expiresAt) <= Date.now()) return;
    try {
      await navigator.clipboard.writeText(`/zmdlog 绑定 ${binding.code}`);
      setMessage("绑定指令已复制，请发送给你信任的 ZmdBot。");
    } catch {
      codeInput.current?.focus();
      codeInput.current?.select();
      setMessage("无法自动复制，请选中绑定码手动复制。");
    }
  }

  return (
    <section className="panel panel-muted binding-code-panel" aria-labelledby="binding-title">
      <div className="eyebrow">账号连接</div>
      <h1 id="binding-title">机器人绑定</h1>
      <p>当前账号：<strong>{nickname}</strong></p>
      <p className="muted">生成绑定码后，将绑定指令发送给 ZmdBot，即可验证并关联你的 ZMDLogs 网站账号。没有战斗记录也可以绑定。</p>
      <div className="panel-inset binding-code-display">
        {binding ? (
          active ? (
            <>
              <label htmlFor="binding-code-value">本次绑定码</label>
              <input id="binding-code-value" ref={codeInput} className="binding-code-value" value={binding.code} readOnly autoComplete="off" spellCheck={false} />
              <span className="muted">剩余有效时间 <strong>{timeLabel}</strong></span>
            </>
          ) : <p role="status">绑定码已过期，请重新生成。</p>
        ) : <p className="muted">点击下方按钮生成一个 10 分钟有效的绑定码。</p>}
      </div>
      <div className="binding-code-actions">
        <button className="button-primary" type="button" disabled={busy} onClick={generate}>
          {busy ? "生成中…" : binding ? "重新生成绑定码" : "生成绑定码"}
        </button>
        {active ? <button className="button-secondary" type="button" onClick={copyCommand}>复制绑定指令</button> : null}
      </div>
      {error ? <p role="alert" className="binding-code-error">{error}</p> : null}
      <p role="status" className="muted">{message}</p>
      <p className="muted">持码者可向机器人证明对本账号的控制权。请只交给你信任的机器人，不要转发给他人。</p>
      <p className="muted">验证只提供账号 ID 和昵称，不提供邮箱或登录凭证。重新生成会使旧码失效；已建立的 QQ 绑定需在机器人侧管理。</p>
    </section>
  );
}
