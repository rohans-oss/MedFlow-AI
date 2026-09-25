"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { get } from "@/lib/api";
import type { Category, Consumable, Department, SupplierListItem } from "@/lib/types";

export const useDepartments = (includeInactive = false) =>
  useQuery({
    queryKey: ["departments", includeInactive],
    queryFn: () => get<Department[]>("/departments", { include_inactive: includeInactive }),
    staleTime: 5 * 60_000,
  });

export const useCategories = () =>
  useQuery({ queryKey: ["categories"], queryFn: () => get<Category[]>("/categories"), staleTime: 5 * 60_000 });

export const useConsumables = () =>
  useQuery({ queryKey: ["consumables"], queryFn: () => get<Consumable[]>("/consumables"), staleTime: 60_000 });

export const useSuppliers = () =>
  useQuery({ queryKey: ["suppliers"], queryFn: () => get<SupplierListItem[]>("/suppliers") });

/** Mutation that toasts and invalidates the given query-key prefixes on success. */
export function useAction<TVars, TRes>(
  fn: (v: TVars) => Promise<TRes>,
  opts: { success?: string | ((r: TRes) => string); invalidate?: string[][]; onSuccess?: (r: TRes) => void } = {},
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (res) => {
      for (const key of opts.invalidate ?? []) qc.invalidateQueries({ queryKey: key });
      if (opts.success) toast.success(typeof opts.success === "function" ? opts.success(res) : opts.success);
      opts.onSuccess?.(res);
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export const STOCK_KEYS = [["inventory"], ["movements"], ["dashboard"], ["alerts"]];

/** RHF setValueAs helpers */
export const asInt = (v: unknown) => (v === "" || v === null || v === undefined ? undefined : Number(v));
export const asOptionalId = (v: unknown) => (v === "" || v === "0" || v === 0 || v == null ? null : Number(v));
export const emptyToNull = (v: unknown) => (typeof v === "string" && v.trim() === "" ? null : v);
