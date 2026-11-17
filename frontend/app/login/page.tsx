"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, auth } from "@/lib/api";
import { Button, ErrorNote } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await api<{ token: string }>(`/api/auth/${mode}`, { body: form });
      auth.set(res.token);
      router.replace("/");
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-lg border border-line bg-card px-3 py-2 text-sm";
  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-4 py-10">
      <h1 className="font-display text-3xl font-semibold tracking-wide">
        ORCHESTRA <span className="text-brass">AI</span>
      </h1>
      <p className="mt-2 text-sm text-muted">One objective. A coordinated AI workforce. A completed outcome.</p>

      <form onSubmit={submit} className="mt-8 space-y-4 rounded-xl border border-line bg-card p-5">
        <h2 className="font-semibold">{mode === "login" ? "Sign in" : "Create your account"}</h2>
        {mode === "register" && (
          <label className="block text-sm">
            Name
            <input className={field} autoComplete="name" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
        )}
        <label className="block text-sm">
          Email
          <input className={field} type="email" required autoComplete="email" value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </label>
        <label className="block text-sm">
          Password
          <input className={field} type="password" required minLength={8} value={form.password}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            onChange={(e) => setForm({ ...form, password: e.target.value })} />
          {mode === "register" && <span className="mt-1 block text-xs text-muted">At least 8 characters.</span>}
        </label>
        <ErrorNote>{error}</ErrorNote>
        <Button variant="primary" type="submit" disabled={busy} className="w-full py-2">
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </Button>
      </form>

      <p className="mt-4 text-center text-sm text-muted">
        {mode === "login" ? "New here?" : "Already have an account?"}{" "}
        <button className="font-medium text-accent underline" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
          {mode === "login" ? "Create an account" : "Sign in"}
        </button>
      </p>
    </main>
  );
}
