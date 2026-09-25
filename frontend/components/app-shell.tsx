"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeftRight,
  Bell,
  Boxes,
  ChevronDown,
  ClipboardList,
  ShieldAlert,
  BadgeCheck,
  Cable,
  FlaskConical,
  ShoppingCart,
  Network,
  Sparkles,
  Building,
  ShieldCheck,
  LayoutDashboard,
  LogOut,
  Menu,
  ScrollText,
  Settings,
  TrendingUp,
  Truck,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

import { Aurora } from "@/components/art";
import { HospitalSwitcher, useSwitchHospital } from "@/components/hospital-switcher";
import { Badge, Button, Card, CardBody, CardHeader, Skeleton } from "@/components/ui/primitives";
import { PERM, useMe } from "@/hooks/use-me";
import { get, post } from "@/lib/api";
import type { Alert, Me, Page } from "@/lib/types";
import { cn, ROLE_LABELS } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: typeof Activity; perm?: string; badge?: boolean };

const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/alerts", label: "Alerts", icon: Bell, badge: true },
      { href: "/assistant", label: "AI assistant", icon: Sparkles },
    ],
  },
  {
    title: "Intelligence",
    items: [
      { href: "/forecasts", label: "Forecasts", icon: TrendingUp },
      { href: "/stockout-risks", label: "Stockout risk", icon: ShieldAlert },
      { href: "/supplier-intelligence", label: "Supplier intelligence", icon: BadgeCheck },
      { href: "/procurement", label: "Procurement", icon: ShoppingCart },
      { href: "/knowledge-graph", label: "Knowledge graph", icon: Network },
    ],
  },
  {
    title: "Operations",
    items: [
      { href: "/inventory", label: "Inventory", icon: Boxes },
      { href: "/movements", label: "Stock movements", icon: ArrowLeftRight },
      { href: "/procedures", label: "Procedures", icon: ClipboardList },
      { href: "/suppliers", label: "Suppliers", icon: Truck },
      { href: "/integrations", label: "Integrations", icon: Cable, perm: PERM.INTEGRATIONS_RUN },
      { href: "/pilots", label: "Pilots", icon: FlaskConical },
    ],
  },
  {
    title: "Administration",
    items: [
      { href: "/settings", label: "Settings", icon: Settings },
      { href: "/audit-logs", label: "Audit log", icon: ScrollText, perm: PERM.READ_AUDIT },
    ],
  },
];
// V8: pages that do not need an active hospital
const TENANT_FREE = ["/organization", "/platform"];

function NoHospital({ me }: { me: Me }) {
  const { switchTo, pending, error } = useSwitchHospital();
  const available = me.memberships.filter((m) => m.available);
  return (
    <Card className="mx-auto max-w-xl" data-testid="no-hospital">
      <CardHeader title="Choose a hospital" description="MedFlow shows one hospital at a time. Pick one of the hospitals you are a member of." />
      <CardBody className="space-y-2">
        {available.map((m) => (
          <Button key={m.hospital_id} variant="outline" className="w-full justify-between" disabled={pending !== null} onClick={() => switchTo(m.hospital_id)}>
            <span>{m.hospital_name} <span className="text-muted-foreground">· {m.organization_name}</span></span>
            <span className="text-xs text-muted-foreground">{ROLE_LABELS[m.role]}</span>
          </Button>
        ))}
        {!available.length && (
          <p className="text-sm text-muted-foreground">
            You are not an active member of any hospital.
            {me.admin_organizations.length > 0 && " You can still administer your organization."}
            {me.is_platform_admin && " You can still manage organizations on the Platform page."}
          </p>
        )}
        {error && <p className="text-sm text-red-600">{error}</p>}
      </CardBody>
    </Card>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const { me, can, isLoading } = useMe();
  const [mobileOpen, setMobileOpen] = useState(false);

  const alerts = useQuery({
    queryKey: ["alerts", "count"],
    queryFn: () => get<Page<Alert>>("/alerts", { status: "OPEN", page_size: 1 }),
    refetchInterval: 60_000,
    enabled: !!me?.hospital,
  });

  const logout = async () => {
    await post("/auth/logout").catch(() => undefined);
    qc.clear();
    router.replace("/login");
    router.refresh();
  };

  const groups = [
    ...(me?.hospital ? NAV_GROUPS : []),
    ...(me?.admin_organizations.length || me?.is_platform_admin
      ? [
          {
            title: me?.hospital ? "Tenancy" : "Administration",
            items: [
              ...(me?.admin_organizations.length ? [{ href: "/organization", label: "Organization", icon: Building }] : []),
              ...(me?.is_platform_admin ? [{ href: "/platform", label: "Platform", icon: ShieldCheck }] : []),
            ] as NavItem[],
          },
        ]
      : []),
  ]
    .map((g) => ({ ...g, items: g.items.filter((n) => !n.perm || can(n.perm)) }))
    .filter((g) => g.items.length);

  const nav = (
    <nav className="nav-scroll relative flex flex-col gap-3.5 overflow-y-auto px-3 pb-3">
      {groups.map((g) => (
        <div key={g.title}>
          <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-teal-200/45">{g.title}</p>
          <div className="flex flex-col gap-0.5">
            {g.items.map((n) => {
              const active = pathname === n.href || pathname.startsWith(`${n.href}/`);
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  onClick={() => setMobileOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "group relative flex items-center gap-3 rounded-xl px-3 py-[7px] text-sm font-medium transition-all duration-200",
                    active
                      ? "bg-gradient-to-r from-white/[0.14] to-white/[0.04] text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.08)]"
                      : "text-teal-50/70 hover:translate-x-0.5 hover:bg-white/[0.06] hover:text-white",
                  )}
                >
                  {active && (
                    <span className="absolute inset-y-1.5 left-0 w-[3px] rounded-full bg-gradient-to-b from-teal-300 to-cyan-400 shadow-[0_0_12px_rgb(45_212_191/0.8)]" aria-hidden />
                  )}
                  <n.icon
                    className={cn(
                      "size-4 shrink-0 transition-colors",
                      active ? "text-teal-300 drop-shadow-[0_0_6px_rgb(45_212_191/0.7)]" : "text-teal-100/50 group-hover:text-teal-200",
                    )}
                  />
                  <span className="flex-1">{n.label}</span>
                  {n.badge && !!alerts.data?.total && (
                    <span className="animate-pulse-ring rounded-full bg-gradient-to-br from-red-500 to-rose-600 px-1.5 text-[11px] font-semibold text-white">
                      {alerts.data.total}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );

  const brand = (
    <Link href="/dashboard" className="group flex h-16 shrink-0 items-center gap-2.5 px-6" onClick={() => setMobileOpen(false)}>
      <span className="relative flex size-9 items-center justify-center rounded-xl bg-gradient-to-br from-teal-300 to-cyan-500 shadow-lg shadow-teal-500/30 transition-transform duration-300 group-hover:rotate-6 group-hover:scale-105">
        <Activity className="size-5 text-brand-950" strokeWidth={2.5} />
        <span className="absolute -right-0.5 -top-0.5 size-2.5 rounded-full border-2 border-brand-900 bg-emerald-400" aria-hidden />
      </span>
      <span className="leading-tight">
        <span className="block text-[15px] font-semibold tracking-tight text-white">MedFlow AI</span>
        <span className="block text-[10px] font-medium uppercase tracking-[0.18em] text-teal-200/60">Supply intelligence</span>
      </span>
    </Link>
  );

  const footer = (
    <div className="relative mx-3 mb-3 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5 text-[11px] leading-relaxed text-teal-50/55">
      <p className="flex items-center gap-1.5 font-medium text-teal-100/80">
        <span className="size-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgb(52_211_153/0.9)]" aria-hidden />
        v10.0 · Pilot &amp; business validation
      </p>
      Operations software — not for clinical decisions.
    </div>
  );

  return (
    <div className="min-h-screen">
      <Aurora />

      {/* Desktop sidebar */}
      <aside className="sidebar-bg fixed inset-y-0 left-0 z-30 hidden w-64 flex-col shadow-2xl shadow-teal-950/30 lg:flex">
        <div className="relative">{brand}</div>
        <div className="relative mt-1 flex min-h-0 flex-1 flex-col">{nav}</div>
        {footer}
      </aside>

      {/* Mobile sidebar */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 animate-fade-in bg-slate-950/50 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
          <aside className="sidebar-bg absolute inset-y-0 left-0 flex w-72 max-w-[85vw] animate-slide-in flex-col shadow-2xl">
            <div className="relative flex items-center justify-between pr-3">
              {brand}
              <button onClick={() => setMobileOpen(false)} aria-label="Close menu" className="rounded-lg p-1.5 text-teal-50/80 hover:bg-white/10">
                <X className="size-4" />
              </button>
            </div>
            <div className="relative flex min-h-0 flex-1 flex-col">{nav}</div>
            {footer}
          </aside>
        </div>
      )}

      <div className="relative z-10 lg:pl-64">
        <header className="glass sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-white/60 px-4 shadow-[0_1px_0_rgb(11_27_43/0.04),0_10px_30px_-20px_rgb(11_27_43/0.25)] lg:px-8">
          <button className="rounded-lg p-1.5 hover:bg-teal-50 lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu">
            <Menu className="size-5" />
          </button>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            {isLoading || !me ? (
              <Skeleton className="h-4 w-48" />
            ) : (
              <>
                <HospitalSwitcher me={me} />
                {me.organization && <span className="hidden truncate text-xs text-muted-foreground md:inline">{me.organization.name}</span>}
                {me.hospital?.is_demo && <Badge tone="violet">Synthetic demo data</Badge>}
              </>
            )}
          </div>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button className="flex items-center gap-2 rounded-xl px-2 py-1.5 text-left transition-colors hover:bg-white/80" data-testid="user-menu">
                <span className="flex size-9 items-center justify-center rounded-full bg-gradient-to-br from-teal-500 to-cyan-700 text-xs font-semibold text-white shadow-md shadow-teal-700/30 ring-2 ring-white">
                  {me?.full_name
                    .split(" ")
                    .map((p) => p[0])
                    .slice(0, 2)
                    .join("")}
                </span>
                <span className="hidden text-sm sm:block">
                  <span className="block font-medium leading-tight">{me?.full_name}</span>
                  <span className="block text-xs text-muted-foreground">
                    {me?.role ? ROLE_LABELS[me.role] : me?.is_platform_admin ? "Platform administrator" : me?.admin_organizations.length ? "Organization administrator" : ""}
                  </span>
                </span>
                <ChevronDown className="size-4 text-muted-foreground" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content align="end" sideOffset={6} className="z-50 w-56 animate-fade-in rounded-xl border border-white/80 bg-white/95 p-1 shadow-xl shadow-teal-950/15 ring-1 ring-slate-900/5 backdrop-blur-xl">
                <div className="px-2 py-1.5 text-xs text-muted-foreground">{me?.email}</div>
                <DropdownMenu.Item asChild>
                  <Link href="/settings?tab=account" className="block rounded-md px-2 py-1.5 text-sm outline-none hover:bg-muted">
                    My account
                  </Link>
                </DropdownMenu.Item>
                <DropdownMenu.Separator className="my-1 h-px bg-border" />
                <DropdownMenu.Item
                  onSelect={logout}
                  className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-red-600 outline-none hover:bg-red-50"
                >
                  <LogOut className="size-4" /> Sign out
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </header>
        <main className="mx-auto max-w-7xl p-4 lg:p-8">
          <div key={pathname} className="page-enter">
            {me && !me.hospital && !TENANT_FREE.some((p) => pathname.startsWith(p)) ? <NoHospital me={me} /> : children}
          </div>
        </main>
      </div>
    </div>
  );
}
