"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowRight, FlaskConical, Loader2, Lock, Mail, ShieldAlert, Sparkles, TrendingUp, Truck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { SupplyNetworkArt } from "@/components/art";
import { Button, Field, Input } from "@/components/ui/primitives";
import { ApiError, post } from "@/lib/api";
import type { Me } from "@/lib/types";

const schema = z.object({
  email: z.email("Enter a valid email"),
  password: z.string().min(1, "Password is required"),
});
type Values = z.infer<typeof schema>;

const DEMO_USERS = [
  ["admin@sunrise.demo", "Administrator"],
  ["procurement@sunrise.demo", "Procurement"],
  ["inventory@sunrise.demo", "Inventory"],
  ["ortho@sunrise.demo", "Ortho OT (dept.)"],
  ["viewer@sunrise.demo", "Viewer"],
] as const;

const HIGHLIGHTS = [
  { icon: TrendingUp, title: "Forecasts", text: "14-day demand per item, with drivers" },
  { icon: ShieldAlert, title: "Stockout risk", text: "Which items run short, and when" },
  { icon: Truck, title: "Suppliers", text: "On-time-in-full, from order history" },
] as const;

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });

  const onSubmit = async (values: Values) => {
    setError(null);
    try {
      const res = await post<{ user: Me }>("/auth/login", values);
      qc.setQueryData(["me"], res.user);
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard");
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not reach the server");
    }
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-brand-950 text-white">
      {/* background: gradient + animated supply network + pulse line */}
      <div className="pointer-events-none absolute inset-0" aria-hidden>
        <div className="absolute inset-0 bg-[radial-gradient(80%_60%_at_15%_20%,rgb(20_184_166/0.35),transparent_60%),radial-gradient(60%_50%_at_90%_80%,rgb(56_189_248/0.25),transparent_60%),radial-gradient(50%_40%_at_60%_10%,rgb(129_140_248/0.18),transparent_60%)]" />
        <SupplyNetworkArt className="absolute inset-0 h-full w-full opacity-70" />
        <div className="absolute inset-0 bg-[linear-gradient(90deg,rgb(4_31_34/0.2),rgb(4_31_34/0.1)_45%,rgb(4_31_34/0.55))]" />
        <div className="absolute inset-x-0 bottom-0 h-40 bg-gradient-to-t from-brand-950 to-transparent" />
      </div>

      <div className="relative mx-auto grid min-h-screen max-w-7xl items-center gap-10 px-5 py-10 lg:grid-cols-[1.1fr_1fr] lg:px-10">
        {/* hero */}
        <div className="flex h-full flex-col justify-between gap-10">
          <div className="flex animate-fade-up items-center gap-2.5">
            <span className="relative flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-teal-300 to-cyan-500 shadow-lg shadow-teal-400/30">
              <Activity className="size-5 text-brand-950" strokeWidth={2.5} />
              <span className="absolute -right-0.5 -top-0.5 size-2.5 animate-ping rounded-full bg-emerald-300" aria-hidden />
            </span>
            <span className="leading-tight">
              <span className="block text-lg font-semibold tracking-tight">MedFlow AI</span>
              <span className="block text-[10px] font-medium uppercase tracking-[0.2em] text-teal-200/70">Hospital supply intelligence</span>
            </span>
          </div>

          <div className="hidden max-w-xl space-y-6 lg:block">
            <p className="inline-flex animate-fade-up items-center gap-2 rounded-full border border-teal-300/25 bg-teal-300/10 px-3 py-1 text-xs font-medium text-teal-100 backdrop-blur [animation-delay:80ms]">
              <Sparkles className="size-3.5 text-teal-300" /> Forecasting · Stockout risk · Procurement
            </p>
            <h2 className="animate-fade-up text-5xl font-semibold leading-[1.05] tracking-tight [animation-delay:160ms]">
              Know what your hospital will run out of —{" "}
              <span className="bg-gradient-to-r from-teal-200 via-cyan-200 to-sky-300 bg-clip-text text-transparent">before it does.</span>
            </h2>
            <p className="max-w-md animate-fade-up text-base leading-relaxed text-teal-50/75 [animation-delay:240ms]">
              Inventory, demand forecasts, stockout risk, supplier reliability and human-approved procurement for hospital consumables — in one
              place.
            </p>
            <div className="grid max-w-lg grid-cols-3 gap-3 pt-2">
              {HIGHLIGHTS.map(({ icon: Icon, title, text }, i) => (
                <div
                  key={title}
                  className="animate-fade-up rounded-2xl border border-white/10 bg-white/[0.06] p-3.5 backdrop-blur-md transition-all duration-300 hover:-translate-y-1 hover:border-teal-300/30 hover:bg-white/[0.1]"
                  style={{ animationDelay: `${320 + i * 90}ms` }}
                >
                  <span className="flex size-8 items-center justify-center rounded-lg bg-gradient-to-br from-teal-300/90 to-cyan-500/90 text-brand-950">
                    <Icon className="size-4" />
                  </span>
                  <p className="mt-2.5 text-sm font-semibold">{title}</p>
                  <p className="mt-0.5 text-xs leading-snug text-teal-50/60">{text}</p>
                </div>
              ))}
            </div>
          </div>

          <p className="hidden text-xs text-teal-100/50 lg:block">Operations &amp; procurement software. Not a clinical decision system.</p>
        </div>

        {/* sign-in card */}
        <div className="flex justify-center lg:justify-end">
          <div className="w-full max-w-md animate-fade-up rounded-3xl border border-white bg-white p-7 text-foreground shadow-2xl shadow-black/40 ring-1 ring-white/20 backdrop-blur-xl [animation-delay:200ms] sm:p-8">
            <div>
              <h1 className="text-gradient text-2xl font-semibold tracking-tight">Sign in</h1>
              <p className="mt-1 text-sm text-muted-foreground">Welcome back — use your hospital account.</p>
            </div>
            <form onSubmit={handleSubmit(onSubmit)} className="mt-6 space-y-4" noValidate>
              <Field label="Email" htmlFor="email" error={errors.email?.message}>
                <div className="relative">
                  <Mail className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                  <Input id="email" type="email" autoComplete="username" className="h-11 pl-9" placeholder="you@hospital.org" {...register("email")} />
                </div>
              </Field>
              <Field label="Password" htmlFor="password" error={errors.password?.message}>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                  <Input id="password" type="password" autoComplete="current-password" className="h-11 pl-9" {...register("password")} />
                </div>
              </Field>
              {error && <p className="animate-fade-in rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-200">{error}</p>}
              <Button type="submit" size="lg" className="group h-11 w-full text-[15px]" disabled={isSubmitting}>
                {isSubmitting ? <Loader2 className="animate-spin" /> : null} Sign in
                {!isSubmitting && <ArrowRight className="transition-transform group-hover:translate-x-0.5" />}
              </Button>
            </form>

            <div className="mt-6 rounded-2xl border border-dashed border-teal-200 bg-gradient-to-br from-teal-50/80 to-cyan-50/60 p-4">
              <p className="flex items-center gap-1.5 text-xs font-medium text-teal-900/80">
                <FlaskConical className="size-3.5" /> Demo accounts (synthetic data) — password{" "}
                <code className="rounded bg-white px-1 text-teal-800 ring-1 ring-teal-200">Demo@1234</code>
              </p>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {DEMO_USERS.map(([email, label]) => (
                  <button
                    key={email}
                    type="button"
                    className="rounded-lg border border-teal-200/80 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 shadow-xs transition-all hover:-translate-y-0.5 hover:border-teal-400 hover:text-teal-800 hover:shadow-md"
                    onClick={() => {
                      setValue("email", email);
                      setValue("password", "Demo@1234");
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <p className="mt-5 text-center text-[11px] text-muted-foreground lg:hidden">Operations &amp; procurement software. Not a clinical decision system.</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
