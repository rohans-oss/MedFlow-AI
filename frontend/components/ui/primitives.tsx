import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

/* ---------------- Button ---------------- */

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-200 active:scale-[0.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0 cursor-pointer",
  {
    variants: {
      variant: {
        default:
          "btn-shine bg-gradient-to-br from-teal-600 to-cyan-700 text-primary-foreground shadow-md shadow-teal-900/20 hover:shadow-lg hover:shadow-teal-700/30 hover:brightness-110",
        outline: "border border-slate-200 bg-white/80 backdrop-blur hover:border-teal-300 hover:bg-teal-50/60 hover:text-teal-900 shadow-xs",
        ghost: "hover:bg-teal-50/70 hover:text-teal-900",
        danger: "btn-shine bg-gradient-to-br from-red-500 to-rose-700 text-white shadow-md shadow-red-900/20 hover:brightness-110",
        subtle: "bg-accent text-primary hover:bg-emerald-100",
      },
      size: {
        default: "h-9 px-4",
        sm: "h-8 px-3 text-xs",
        lg: "h-10 px-5",
        icon: "size-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  },
);
Button.displayName = "Button";

/* ---------------- Inputs ---------------- */

const fieldBase =
  "w-full rounded-lg border border-slate-200 bg-white/90 px-3 text-sm shadow-xs transition-[border-color,box-shadow] duration-200 placeholder:text-muted-foreground hover:border-slate-300 focus-visible:border-teal-500 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-teal-500/15 disabled:opacity-60";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(fieldBase, "h-9", className)} {...props} />,
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref} className={cn(fieldBase, "min-h-20 py-2", className)} {...props} />
  ),
);
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select ref={ref} className={cn(fieldBase, "h-9 pr-8", className)} {...props}>
      {children}
    </select>
  ),
);
Select.displayName = "Select";

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-sm font-medium text-slate-700", className)} {...props} />;
}

export function Field({
  label,
  error,
  hint,
  children,
  className,
  htmlFor,
}: {
  label: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
  htmlFor?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? (
        <p className="text-xs text-danger">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

/* ---------------- Card ---------------- */

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("card-surface min-w-0 rounded-2xl border border-white/80 ring-1 ring-slate-900/5", className)} {...props} />;
}

export function CardHeader({
  title,
  description,
  action,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-slate-200/70 px-5 py-4", className)}>
      <div className="min-w-0 flex-1 basis-56">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <span className="h-3.5 w-1 shrink-0 rounded-full bg-gradient-to-b from-teal-400 to-cyan-600" aria-hidden />
          {title}
        </h3>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      {action && <div className="max-w-full">{action}</div>}
    </div>
  );
}

export function CardBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...props} />;
}

/* ---------------- Badge ---------------- */

const badgeVariants = cva("inline-flex items-center whitespace-nowrap gap-1 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset", {
  variants: {
    tone: {
      neutral: "bg-slate-50 text-slate-700 ring-slate-200",
      red: "bg-red-50 text-red-700 ring-red-200",
      amber: "bg-amber-50 text-amber-800 ring-amber-200",
      green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
      blue: "bg-sky-50 text-sky-700 ring-sky-200",
      violet: "bg-violet-50 text-violet-700 ring-violet-200",
      orange: "bg-orange-50 text-orange-700 ring-orange-200",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({
  className,
  tone,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

/* ---------------- Table ---------------- */

export function Table({ className, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full text-sm", className)} {...props} />
    </div>
  );
}
export function THead(props: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className="border-b border-slate-200/70 bg-slate-50/70 text-left text-[11px] uppercase tracking-wider text-muted-foreground" {...props} />;
}
export function TH({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn("px-4 py-2.5 font-medium whitespace-nowrap", className)} {...props} />;
}
export function TR({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("border-b border-slate-100 transition-colors last:border-0 hover:bg-teal-50/40", className)} {...props} />;
}
export function TD({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-4 py-2.5 align-middle", className)} {...props} />;
}

/* ---------------- Misc ---------------- */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton-shimmer animate-shimmer rounded-xl", className)} />;
}

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center animate-fade-in">
      <span className="mb-1 flex size-11 items-center justify-center rounded-2xl bg-gradient-to-br from-teal-50 to-cyan-100 text-teal-700 ring-1 ring-teal-200/60" aria-hidden>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12h4l2-5 4 10 2-5h6" />
        </svg>
      </span>
      <p className="text-sm font-medium">{title}</p>
      {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-gradient text-2xl font-semibold tracking-tight">{title}</h1>
        <div className="mt-2 h-1 w-14 rounded-full bg-gradient-to-r from-teal-400 via-cyan-500 to-sky-400 bar-grow" aria-hidden />
        {description && <p className="mt-2 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const STAT_TONES = {
  default: { value: "text-slate-900", chip: "from-teal-500 to-cyan-600 shadow-teal-600/30", glow: "from-teal-400/25" },
  red: { value: "text-red-600", chip: "from-rose-500 to-red-600 shadow-red-600/30", glow: "from-rose-400/25" },
  amber: { value: "text-amber-600", chip: "from-amber-400 to-orange-500 shadow-amber-600/30", glow: "from-amber-300/30" },
  green: { value: "text-emerald-600", chip: "from-emerald-400 to-teal-600 shadow-emerald-600/30", glow: "from-emerald-300/25" },
} as const;

export function Stat({
  label,
  value,
  sub,
  tone = "default",
  icon,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "default" | "red" | "amber" | "green";
  icon?: React.ReactNode;
}) {
  const t = STAT_TONES[tone];
  return (
    <Card className="lift group relative overflow-hidden p-4">
      <div className={cn("pointer-events-none absolute -right-10 -top-10 size-32 rounded-full bg-gradient-to-br to-transparent blur-2xl transition-transform duration-500 group-hover:scale-125", t.glow)} aria-hidden />
      <div className="relative flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground">
        {label}
        {icon && (
          <span className={cn("flex size-8 items-center justify-center rounded-xl bg-gradient-to-br text-white shadow-md transition-transform duration-300 group-hover:-rotate-6 group-hover:scale-110 [&_svg]:size-4", t.chip)}>
            {icon}
          </span>
        )}
      </div>
      <div className={cn("relative mt-2 text-2xl font-semibold tracking-tight", t.value)}>{value}</div>
      {sub && <div className="relative mt-1 text-xs text-muted-foreground">{sub}</div>}
    </Card>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between border-t border-slate-200/70 px-4 py-3 text-xs text-muted-foreground">
      <span>
        {total === 0 ? "No results" : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}`}
      </span>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </div>
  );
}
