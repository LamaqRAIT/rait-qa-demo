"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { clsx } from "clsx";
import { login, fetchDemoUsers, getStoredUser } from "@/lib/api";
import type { UserRole } from "@/lib/types";

const ROLE_COLORS: Record<UserRole, string> = {
  super_admin:  "text-yellow",
  qa_manager:   "text-blue",
  qa_engineer:  "text-green",
  developer:    "text-cream/60",
  system_agent: "text-cream/40",
};

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [demoUsers, setDemoUsers] = useState<Array<{ email: string; password: string; role: string; full_name: string }>>([]);

  useEffect(() => {
    // Already logged in → go to dashboard
    if (getStoredUser()) { router.push("/"); return; }
    fetchDemoUsers().then(setDemoUsers);
  }, [router]);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      router.push("/");
    } catch {
      setError("Invalid email or password. Use the demo credentials below.");
    } finally {
      setLoading(false);
    }
  }

  async function quickLogin(u: { email: string; password: string }) {
    setEmail(u.email);
    setPassword(u.password);
    setError("");
    setLoading(true);
    try {
      await login(u.email, u.password);
      router.push("/");
    } catch {
      setError("Login failed. Check the backend is reachable.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-canvas flex flex-col items-center justify-center p-6">
      {/* Logo */}
      <div className="flex items-center gap-2 mb-8">
        <span className="w-2.5 h-2.5 rounded-full bg-green" />
        <span className="text-[17px] font-semibold tracking-tight">RAIT QA Agent</span>
      </div>

      <div className="w-full max-w-[380px]">
        {/* Card */}
        <div className="bg-elevated border border-cream/8 rounded-xl p-6 shadow-lg">
          <h1 className="text-[15px] font-semibold mb-1">Sign in to your account</h1>
          <p className="text-[12px] text-cream/35 mb-5">Demo credentials are listed below the form.</p>

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="text-[11px] font-medium text-cream/45 uppercase tracking-wide block mb-1.5">
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
                required
                className={clsx(
                  "w-full h-10 px-3 rounded-lg border bg-card text-[13px] text-cream outline-none transition",
                  "border-cream/12 focus:border-blue/50 focus:ring-1 focus:ring-blue/20",
                  "placeholder:text-cream/20"
                )}
              />
            </div>
            <div>
              <label className="text-[11px] font-medium text-cream/45 uppercase tracking-wide block mb-1.5">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                className={clsx(
                  "w-full h-10 px-3 rounded-lg border bg-card text-[13px] text-cream outline-none transition",
                  "border-cream/12 focus:border-blue/50 focus:ring-1 focus:ring-blue/20",
                  "placeholder:text-cream/20"
                )}
              />
            </div>

            {error && (
              <p className="text-[12px] text-red animate-fade-in">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className={clsx(
                "w-full h-10 rounded-lg text-[13px] font-semibold transition",
                "bg-blue text-canvas hover:bg-blue/90 disabled:opacity-50 disabled:cursor-not-allowed"
              )}
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>

        {/* Demo credentials */}
        <div className="mt-4 bg-elevated border border-cream/8 rounded-xl p-4">
          <p className="text-[11px] text-cream/30 uppercase tracking-widest font-semibold mb-3">
            Demo Credentials
          </p>
          <div className="space-y-2">
            {demoUsers.length === 0 ? (
              <div className="space-y-1.5">
                {[
                  { full_name: "Super Admin", role: "super_admin", email: "admin@rait.ai", password: "admin123" },
                  { full_name: "QA Manager",  role: "qa_manager",  email: "manager@rait.ai", password: "manager123" },
                  { full_name: "QA Engineer", role: "qa_engineer", email: "qa@rait.ai", password: "qa123" },
                  { full_name: "Developer",   role: "developer",   email: "dev@rait.ai", password: "dev123" },
                ].map((u) => (
                  <button
                    key={u.email}
                    onClick={() => quickLogin(u)}
                    className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-card hover:bg-surface transition text-left"
                  >
                    <div className="flex-1">
                      <span className="text-[13px] font-medium text-cream/80">{u.full_name}</span>
                      <span className={clsx("ml-2 text-[10px] font-semibold uppercase", ROLE_COLORS[u.role as UserRole])}>
                        {u.role.replace("_", " ")}
                      </span>
                    </div>
                    <span className="text-[11px] font-mono text-cream/25">{u.email}</span>
                  </button>
                ))}
              </div>
            ) : demoUsers.map((u) => (
              <button
                key={u.email}
                onClick={() => quickLogin(u)}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-card hover:bg-surface transition text-left"
              >
                <div className="flex-1">
                  <span className="text-[13px] font-medium text-cream/80">{u.full_name}</span>
                  <span className={clsx("ml-2 text-[10px] font-semibold uppercase", ROLE_COLORS[u.role as UserRole])}>
                    {u.role.replace("_", " ")}
                  </span>
                </div>
                <span className="text-[11px] font-mono text-cream/25">{u.email}</span>
              </button>
            ))}
          </div>
          <p className="text-[10px] text-cream/20 mt-3">Click any user to auto-fill credentials</p>
        </div>
      </div>
    </div>
  );
}
