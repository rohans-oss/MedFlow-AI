"""V5.3 — scenario selection with OR-Tools CP-SAT.

Every at-risk item has a set of evaluated scenarios (no order, supplier × quantity, split orders), each with an
expected total cost from the configurable cost model and a cash outlay. The solver picks exactly one scenario per
item to minimise the summed expected cost, subject to an optional purchasing budget across all items:

    minimise   Σ_i Σ_j cost_ij · x_ij
    subject to Σ_j x_ij = 1                      for every item i
               Σ_i Σ_j cash_ij · x_ij ≤ budget  (only when a budget is configured)
               x_ij ∈ {0, 1}

Without a budget this is the per-item minimum; with a binding budget the solver trades cost against cash across items
(which is why a solver is used instead of sorting). Costs are in paise (integers). Deterministic: one worker, fixed seed.
"""

from dataclasses import dataclass

from ortools.sat.python import cp_model


@dataclass
class Choice:
    cost: float  # expected total cost (₹)
    cash: float  # purchase outlay (₹)


@dataclass
class Solution:
    picks: list[int]  # chosen scenario index per item
    status: str  # OPTIMAL | FEASIBLE | INFEASIBLE | …
    objective: float  # ₹
    cash: float  # ₹
    budget: float | None
    budget_binding: bool  # the unconstrained choice would exceed the budget


def _paise(v: float) -> int:
    return int(round(v * 100))


def choose(items: list[list[Choice]], budget: float | None = None, time_limit_s: float = 10.0) -> Solution:
    if not items:
        return Solution([], "OPTIMAL", 0.0, 0.0, budget, False)
    free = [min(range(len(ch)), key=lambda j: (ch[j].cost, ch[j].cash)) for ch in items]
    free_cash = sum(items[i][j].cash for i, j in enumerate(free))
    m = cp_model.CpModel()
    x = [[m.new_bool_var(f"x_{i}_{j}") for j in range(len(ch))] for i, ch in enumerate(items)]
    for row in x:
        m.add_exactly_one(row)
    if budget is not None:
        m.add(sum(_paise(items[i][j].cash) * x[i][j] for i in range(len(items)) for j in range(len(items[i])))
              <= _paise(budget))
    m.minimize(sum(_paise(items[i][j].cost) * x[i][j] for i in range(len(items)) for j in range(len(items[i]))))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    st = solver.solve(m)
    name = solver.status_name(st)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Solution(free, name, sum(items[i][j].cost for i, j in enumerate(free)), free_cash, budget, True)
    picks = [next(j for j in range(len(row)) if solver.value(row[j])) for row in x]
    return Solution(picks, name, sum(items[i][j].cost for i, j in enumerate(picks)),
                    sum(items[i][j].cash for i, j in enumerate(picks)), budget,
                    budget is not None and free_cash > budget + 1e-6)
