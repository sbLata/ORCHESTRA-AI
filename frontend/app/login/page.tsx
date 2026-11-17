"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, auth } from "@/lib/api";
import { Button, ErrorNote, Logo } from "@/components/ui";

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

  const field = "mt-1 w-full rounded-lg border border-line bg-card px-3 py-2.5 text-sm font-normal shadow-xs";
  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <section className="brand-gradient hidden flex-col justify-between p-12 text-white lg:flex">
        <Logo light />
        <div>
          <h1 className="max-w-md text-5xl font-bold leading-[1.08] tracking-tight">
            One objective. A coordinated AI workforce.
          </h1>
          <p className="mt-5 max-w-md text-lg text-white/85">
            Describe the outcome you need. A team of specialized agents plans it, does the work, checks it, and asks
            you before anything sensitive.
          </p>
        </div>
        <ul className="flex flex-wrap gap-2 text-sm">
          {["Research", "Planning", "Execution", "QA", "Supervisor"].map((a) => (
            <li key={a} className="rounded-full bg-white/15 px-3 py-1 ring-1 ring-white/25">{a}</li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col justify-center px-6 py-10 sm:px-12">
        <div className="mx-auto w-full max-w-sm">
          <div className="lg:hidden"><Logo /></div>
          <h2 className="mt-8 text-2xl font-bold tracking-tight lg:mt-0">
            {mode === "login" ? "Sign in to your workspace" : "Create your account"}
          </h2>
          <p className="mt-1 text-sm text-muted">
            {mode === "login" ? "New here?" : "Already have an account?"}{" "}
            <button className="font-medium text-accent hover:underline" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
              {mode === "login" ? "Create an account" : "Sign in"}
            </button>
          </p>

          <form onSubmit={submit} className="mt-6 space-y-4">
            {mode === "register" && (
              <label className="block text-sm font-medium">
                Name
                <input className={field} autoComplete="name" value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </label>
            )}
            <label className="block text-sm font-medium">
              Email
              <input className={field} type="email" required autoComplete="email" value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </label>
            <label className="block text-sm font-medium">
              Password
              <input className={field} type="password" required minLength={8} value={form.password}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                onChange={(e) => setForm({ ...form, password: e.target.value })} />
              {mode === "register" && <span className="mt-1 block text-xs font-normal text-muted">At least 8 characters.</span>}
            </label>
            <ErrorNote>{error}</ErrorNote>
            <Button variant="primary" type="submit" disabled={busy} className="w-full py-2.5">
              {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
            </Button>
          </form>
        </div>
      </section>
    </main>
  );
}
