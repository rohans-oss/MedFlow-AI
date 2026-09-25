"use client";

import { useQuery } from "@tanstack/react-query";

import { get, setActiveHospital } from "@/lib/api";
import type { Me } from "@/lib/types";

export const PERM = {
  READ: "read",
  MANAGE_USERS: "users:manage",
  MANAGE_HOSPITAL: "hospital:manage",
  MANAGE_DEPARTMENTS: "departments:manage",
  READ_AUDIT: "audit:read",
  MANAGE_CATALOG: "catalog:manage",
  MANAGE_SUPPLIERS: "suppliers:manage",
  STOCK_RECEIVE: "stock:receive",
  STOCK_ISSUE: "stock:issue",
  STOCK_ISSUE_OWN: "stock:issue:own",
  MANAGE_ALERTS: "alerts:manage",
  TRAIN_FORECASTS: "forecasts:train",
  MANAGE_PROCEDURES: "procedures:manage",
  MANAGE_PROCEDURES_OWN: "procedures:manage:own",
  TRAIN_RISK: "risk:train",
  PROCUREMENT_RECOMMEND: "procurement:recommend",
  PROCUREMENT_APPROVE: "procurement:approve",
  PROCUREMENT_CONFIGURE: "procurement:configure",
  GRAPH_SYNC: "graph:sync",
  INTEGRATIONS_RUN: "integrations:run",
  INTEGRATIONS_MANAGE: "integrations:manage",
  PILOTS_MANAGE: "pilots:manage",
  PILOTS_CONTRIBUTE: "pilots:contribute",
} as const;

export function useMe() {
  const query = useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/auth/me"), staleTime: 5 * 60_000 });
  if (query.data) setActiveHospital(query.data.hospital?.id ?? null); // V8: stale-tab guard header
  const perms = new Set(query.data?.permissions ?? []);
  const can = (...p: string[]) => p.some((x) => perms.has(x));
  return { ...query, me: query.data, can };
}
