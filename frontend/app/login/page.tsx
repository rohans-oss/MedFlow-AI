"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Activity, Loader2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Card, Field, Input } from "@/components/ui/primitives";
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
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="hidden flex-col justify-between bg-teal-900 p-10 text-teal-50 lg:flex">
        <div className="flex items-center gap-2 text-lg font-semibold">
          <Activity className="size-5" /> MedFlow AI
        </div>
        <div className="max-w-md space-y-4">
          <h2 className="text-3xl font-semibold leading-tight text-white">Know what your hospital will run out of — before it does.</h2>
          <p className="text-teal-100/80">
            Inventory, stock movements, suppliers and alerts for hospital consumables. Version 1: working inventory MVP.
          </p>
        </div>
        <p className="text-xs text-teal-200/60">Operations &amp; procurement software. Not a clinical decision system.</p>
      </div>

      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm space-y-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
            <p className="mt-1 text-sm text-muted-foreground">Use your hospital account.</p>
          </div>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <Field label="Email" htmlFor="email" error={errors.email?.message}>
              <Input id="email" type="email" autoComplete="username" {...register("email")} />
            </Field>
            <Field label="Password" htmlFor="password" error={errors.password?.message}>
              <Input id="password" type="password" autoComplete="current-password" {...register("password")} />
            </Field>
            {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
            <Button type="submit" className="w-full" disabled={isSubmitting}>
              {isSubmitting && <Loader2 className="animate-spin" />} Sign in
            </Button>
          </form>

          <Card className="p-4">
            <p className="text-xs font-medium text-muted-foreground">
              Demo accounts (synthetic data) — password <code className="rounded bg-muted px-1">Demo@1234</code>
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {DEMO_USERS.map(([email, label]) => (
                <button
                  key={email}
                  type="button"
                  className="rounded-md border px-2 py-1 text-xs hover:bg-muted"
                  onClick={() => {
                    setValue("email", email);
                    setValue("password", "Demo@1234");
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </Card>
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
