"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { buildApiUrl } from "../../lib/api/client";
import type { AuthUser } from "../../lib/api/types";

type AuthNavProps = {
  currentUser?: AuthUser | null;
};

export function AuthNav({ currentUser: initialCurrentUser = null }: AuthNavProps) {
  const pathname = usePathname();
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(initialCurrentUser);
  const [displayNickname, setDisplayNickname] = useState(currentUser?.nickname ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadCurrentUser() {
      try {
        const response = await fetch(buildApiUrl("/api/auth/me"), {
          credentials: "include",
          cache: "no-store",
        });
        if (!response.ok) {
          if (!cancelled) {
            setCurrentUser(null);
          }
          return;
        }
        const data = (await response.json()) as { user?: AuthUser | null };
        if (!cancelled) {
          setCurrentUser(data.user ?? null);
        }
      } catch {
        if (!cancelled) {
          setCurrentUser(null);
        }
      }
    }

    loadCurrentUser();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setDisplayNickname(currentUser?.nickname ?? "");
  }, [currentUser?.nickname]);

  useEffect(() => {
    function handleNicknameUpdated(event: Event) {
      const nextNickname = (event as CustomEvent<string>).detail;
      if (nextNickname) {
        setDisplayNickname(nextNickname);
      }
    }

    window.addEventListener("auth-nickname-updated", handleNicknameUpdated);
    return () => window.removeEventListener("auth-nickname-updated", handleNicknameUpdated);
  }, []);

  useEffect(() => {
    if (!menuOpen) {
      return;
    }

    function handlePointerDown(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMenuOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [menuOpen]);

  async function handleLogout() {
    setSubmitting(true);
    setMenuOpen(false);
    try {
      const response = await fetch(buildApiUrl("/api/auth/logout"), {
        method: "POST",
        credentials: "include",
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error("注销失败。");
      }
      window.location.assign("/login");
      return;
    } catch {
      setMenuOpen(true);
    } finally {
      setSubmitting(false);
    }
  }

  if (!currentUser) {
    return (
      <Link className={`nav-link${pathname === "/login" ? " is-active" : ""}`} href="/login">
        登录
      </Link>
    );
  }

  return (
    <div className="auth-menu" ref={menuRef}>
      <button
        aria-expanded={menuOpen}
        className={`auth-menu-trigger${menuOpen ? " is-open" : ""}`}
        onClick={() => setMenuOpen((current) => !current)}
        type="button"
      >
        <span>{displayNickname || currentUser.nickname}</span>
        <span className="auth-menu-caret" aria-hidden="true">
          ▾
        </span>
      </button>

      {menuOpen ? (
        <div className="auth-menu-panel">
          <Link
            className={`auth-menu-item${pathname === "/manage" || pathname === "/records" ? " is-active" : ""}`}
            href="/manage"
            onClick={() => setMenuOpen(false)}
          >
            管理
          </Link>
          <Link
            className={`auth-menu-item${pathname === "/account/binding" ? " is-active" : ""}`}
            href="/account/binding"
            onClick={() => setMenuOpen(false)}
          >
            机器人绑定
          </Link>
          {currentUser.isAdmin ? (
            <Link
              className={`auth-menu-item${pathname === "/admin" ? " is-active" : ""}`}
              href="/admin"
              onClick={() => setMenuOpen(false)}
            >
              后台管理
            </Link>
          ) : null}
          <button
            className="auth-menu-item"
            disabled={submitting}
            onClick={handleLogout}
            type="button"
          >
            {submitting ? "注销中..." : "注销"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
