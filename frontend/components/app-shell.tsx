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

import { HospitalSwitcher, useSwitchHospital } from "@/components/hospital-switcher";
import { Badge, Button, Card, CardBody, CardHeader, Skeleton } from "@/components/ui/primitives";
import { PERM, useMe } from "@/hooks/use-me";
import { get, post } from "@/lib/api";
import type { Alert, Me, Page } from "@/lib/types";
import { cn, ROLE_LABELS } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/inventory", label: "Inventory", icon: Boxes },
  { href: "/forecasts", label: "Forecasts", icon: TrendingUp },
  { href: "/procedures", label: "Procedures", icon: ClipboardList },
  { href: "/stockout-risks", label: "Stockout risk", icon: ShieldAlert },
  { href: "/supplier-intelligence", label: "Supplier intelligence", icon: BadgeCheck },
  { href: "/procurement", label: "Procurement", icon: ShoppingCart },
  { href: "/knowledge-graph", label: "Knowledge graph", icon: Network },
  { href: "/assistant", label: "AI assistant", icon: Sparkles },
  { href: "/movements", label: "Stock movements", icon: ArrowLeftRight },
  { href: "/suppliers", label: "Suppliers", icon: Truck },
  { href: "/integrations", label: "Integrations", icon: Cable, perm: PERM.INTEGRATIONS_RUN },
  { href: "/pilots", label: "Pilots", icon: FlaskConical },
  { href: "/alerts", label: "Alerts", icon: Bell, badge: true },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/audit-logs", label: "Audit log", icon: ScrollText, perm: PERM.READ_AUDIT },
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

  const nav = (
    <nav className="flex flex-col gap-0.5 px-3">
      {[...(me?.hospital ? NAV : []),
        ...(me?.admin_organizations.length ? [{ href: "/organization", label: "Organization", icon: Building }] : []),
        ...(me?.is_platform_admin ? [{ href: "/platform", label: "Platform", icon: ShieldCheck }] : []),
      ].filter((n) => !("perm" in n) || !n.perm || can(n.perm)).map((n) => {
        const active = pathname === n.href || pathname.startsWith(`${n.href}/`);
        return (
          <Link
            key={n.href}
            href={n.href}
            onClick={() => setMobileOpen(false)}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              active ? "bg-teal-50 text-teal-800" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
            )}
          >
            <n.icon className="size-4" />
            <span className="flex-1">{n.label}</span>
            {"badge" in n && n.badge && !!alerts.data?.total && (
              <span className="rounded-full bg-red-600 px-1.5 text-[11px] font-semibold text-white">{alerts.data.total}</span>
            )}
          </Link>
        );
      })}
    </nav>
  );

  const brand = (
    <div className="flex h-14 items-center gap-2 px-6 font-semibold text-teal-800">
      <Activity className="size-5" /> MedFlow AI
    </div>
  );

  return (
    <div className="min-h-screen">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r bg-white lg:flex">
        {brand}
        <div className="mt-2 flex-1">{nav}</div>
        <div className="border-t p-4 text-[11px] leading-relaxed text-muted-foreground">
          v10.0 · Pilot & business validation
          <br />
          Operations software — not for clinical decisions.
        </div>
      </aside>

      {/* Mobile sidebar */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 bg-white shadow-xl">
            <div className="flex items-center justify-between pr-3">
              {brand}
              <button onClick={() => setMobileOpen(false)} aria-label="Close menu" className="rounded p-1 hover:bg-muted">
                <X className="size-4" />
              </button>
            </div>
            {nav}
          </aside>
        </div>
      )}

      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-white/90 px-4 backdrop-blur lg:px-8">
          <button className="rounded p-1.5 hover:bg-muted lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu">
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
              <button className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-muted" data-testid="user-menu">
                <span className="flex size-8 items-center justify-center rounded-full bg-teal-700 text-xs font-semibold text-white">
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
              <DropdownMenu.Content align="end" sideOffset={6} className="z-50 w-56 rounded-lg border bg-white p-1 shadow-lg">
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
          {me && !me.hospital && !TENANT_FREE.some((p) => pathname.startsWith(p)) ? <NoHospital me={me} /> : children}
        </main>
      </div>
    </div>
  );
}
