"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useQueryClient } from "@tanstack/react-query";
import { Building2, Check, ChevronDown, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/primitives";
import { ApiError, post, setActiveHospital } from "@/lib/api";
import type { Me, MembershipBrief } from "@/lib/types";
import { ROLE_LABELS } from "@/lib/utils";

/** V8 — the active hospital, and the hospitals this account can switch to. Switching is a server-side operation
 * (membership verified, new session token bound to the new hospital); the client then drops every cached query so
 * nothing from the previous hospital can remain on screen. */
export function useSwitchHospital() {
  const qc = useQueryClient();
  const router = useRouter();
  const [pending, setPending] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const switchTo = async (hospitalId: number) => {
    setPending(hospitalId);
    setError(null);
    try {
      const me = await post<Me>(`/hospitals/${hospitalId}/switch`);
      setActiveHospital(me.hospital?.id ?? null);
      qc.clear(); // no data of the previous hospital survives in the cache
      qc.setQueryData(["me"], me);
      router.push("/dashboard");
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPending(null);
    }
  };
  return { switchTo, pending, error };
}

export function HospitalSwitcher({ me }: { me: Me }) {
  const { switchTo, pending } = useSwitchHospital();
  const available = me.memberships.filter((m) => m.available);
  const byOrg = new Map<string, MembershipBrief[]>();
  for (const m of me.memberships) byOrg.set(m.organization_name, [...(byOrg.get(m.organization_name) ?? []), m]);
  const label = me.hospital?.name ?? "No hospital selected";

  if ((available.length <= 1 && me.hospital) || me.memberships.length === 0) {
    return (
      <span className="flex min-w-0 items-center gap-2" data-testid="active-hospital">
        <Building2 className="size-4 shrink-0 text-teal-700" />
        <span className="truncate text-sm font-medium">{label}</span>
      </span>
    );
  }
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className="flex min-w-0 items-center gap-2 rounded-xl border border-slate-200 bg-white/70 px-2.5 py-1.5 text-left text-sm shadow-xs transition-all hover:border-teal-300 hover:bg-white hover:shadow-md"
          data-testid="hospital-switcher"
          aria-label="Switch hospital"
        >
          <Building2 className="size-4 shrink-0 text-teal-700" />
          <span className="truncate font-medium" data-testid="active-hospital">{label}</span>
          {pending ? <Loader2 className="size-4 animate-spin" /> : <ChevronDown className="size-4 text-muted-foreground" />}
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="start" sideOffset={6} className="z-50 w-80 rounded-xl border border-white/80 bg-white/95 p-1 shadow-xl shadow-teal-950/15 ring-1 ring-slate-900/5 backdrop-blur-xl animate-fade-in">
          {[...byOrg.entries()].map(([org, ms]) => (
            <div key={org}>
              <div className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{org}</div>
              {ms.map((m) => (
                <DropdownMenu.Item
                  key={m.hospital_id}
                  disabled={!m.available}
                  onSelect={() => m.available && m.hospital_id !== me.hospital?.id && switchTo(m.hospital_id)}
                  data-testid={`switch-${m.hospital_code}`}
                  className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none hover:bg-muted data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50"
                >
                  <span className="w-4">{m.hospital_id === me.hospital?.id && <Check className="size-4 text-teal-700" />}</span>
                  <span className="flex-1">
                    <span className="block font-medium leading-tight">{m.hospital_name}</span>
                    <span className="block text-xs text-muted-foreground">{ROLE_LABELS[m.role]}</span>
                  </span>
                  {!m.available && <Badge tone="amber">Suspended</Badge>}
                </DropdownMenu.Item>
              ))}
            </div>
          ))}
          <p className="px-2 pb-1 pt-2 text-[11px] leading-snug text-muted-foreground">
            Each hospital&apos;s data is separate. Switching reloads everything for the selected hospital.
          </p>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
