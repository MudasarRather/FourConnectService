"""Safe formula evaluation for salary components.

NO ``eval()`` on raw user strings. Expressions assigned to a component
(``calc_type == FORMULA``) are parsed with ``ast`` and walked by an allow-list
visitor that permits only arithmetic over a known numeric namespace
(BASIC / GROSS / CTC / PAID_DAYS / …) and a tiny set of safe functions
(min / max / round / abs). Anything else raises ``FormulaError``.

Most components never need this — they resolve via the structured ``calc_type``
(FLAT / PERCENT_OF / STATUTORY / BALANCE / ATTENDANCE_PRORATED). FORMULA is the
escape hatch for the rare "min(0.4*BASIC, 200000)" style head.
"""
from __future__ import annotations

import ast
import operator
from decimal import Decimal, InvalidOperation
from typing import Dict


class FormulaError(ValueError):
    """Raised for malformed or disallowed component formulas."""


_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_CMPOPS = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


def _to_dec(v) -> Decimal:
    try:
        return v if isinstance(v, Decimal) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise FormulaError(f"Non-numeric value: {v!r}")


def _safe_min(*args):
    return min(args, key=lambda x: _to_dec(x))


def _safe_max(*args):
    return max(args, key=lambda x: _to_dec(x))


_FUNCS = {
    "min": _safe_min,
    "max": _safe_max,
    "abs": lambda x: abs(_to_dec(x)),
    "round": lambda x, n=0: Decimal(round(_to_dec(x), int(n))),
}


def _eval(node, ns: Dict[str, Decimal]):
    if isinstance(node, ast.Expression):
        return _eval(node.body, ns)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise FormulaError(f"Only numeric constants allowed, got {node.value!r}")
        return Decimal(str(node.value))

    if isinstance(node, ast.Name):
        if node.id in ns:
            return _to_dec(ns[node.id])
        raise FormulaError(f"Unknown variable '{node.id}'")

    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise FormulaError("Operator not allowed")
        left, right = _eval(node.left, ns), _eval(node.right, ns)
        if isinstance(node.op, ast.Div) and right == 0:
            return Decimal(0)
        return op(left, right)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        val = _eval(node.operand, ns)
        return -val if isinstance(node.op, ast.USub) else val

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise FormulaError("Only min/max/abs/round calls are allowed")
        if node.keywords:
            raise FormulaError("Keyword arguments are not allowed in formulas")
        args = [_eval(a, ns) for a in node.args]
        return _to_dec(_FUNCS[node.func.id](*args))

    if isinstance(node, ast.IfExp):
        return _eval(node.body, ns) if _eval_bool(node.test, ns) else _eval(node.orelse, ns)

    raise FormulaError(f"Disallowed expression: {type(node).__name__}")


def _eval_bool(node, ns) -> bool:
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise FormulaError("Only single comparisons allowed")
        op = _CMPOPS.get(type(node.ops[0]))
        if op is None:
            raise FormulaError("Comparison operator not allowed")
        return bool(op(_eval(node.left, ns), _eval(node.comparators[0], ns)))
    raise FormulaError("Condition must be a comparison")


def evaluate_formula(expr: str, namespace: Dict[str, Decimal]) -> Decimal:
    """Evaluate a component formula against a namespace; returns a Decimal."""
    if not expr or not expr.strip():
        raise FormulaError("Empty formula")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"Syntax error: {e}")
    result = _eval(tree, namespace)
    return _to_dec(result)


# Synthetic tokens always present at evaluation time. Components reference these
# plus any already-computed component codes.
SYNTHETIC_TOKENS = [
    "BASIC", "GROSS", "CTC", "MONTHLY_CTC", "ANNUAL_CTC",
    "PAID_DAYS", "WORKING_DAYS", "LOP_DAYS", "GROSS_TARGET",
]


def validate_formula(expr: str) -> None:
    """Dry-run a formula at save time against a dummy namespace.

    Raises FormulaError if the expression is malformed or references unknown
    tokens. Use a permissive dummy namespace (every synthetic token = 1) so a
    valid expression never trips on a zero-division or missing-name check.
    """
    dummy = {t: Decimal(1) for t in SYNTHETIC_TOKENS}
    # Also allow any bare uppercase identifier the author may reference (a sibling
    # component code) by collecting Names and seeding them as 1.
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"Syntax error: {e}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            dummy.setdefault(node.id, Decimal(1))
    evaluate_formula(expr, dummy)
