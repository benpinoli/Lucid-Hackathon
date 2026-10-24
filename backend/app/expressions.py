"""Safe arithmetic expressions for diagrams, mirrored by frontend/src/expressions.ts.

Grammar (no eval, no attribute access, bounded size and depth):
    expr  := term (("+" | "-") term)*
    term  := unary (("*" | "/") unary)*
    unary := ("+" | "-") unary | power
    power := primary ("^" unary)?
    primary := number | name | name "(" expr ("," expr)? ")" | "(" expr ")"
Names are single-letter variables (a-z except e), the constants pi and e, or allowlisted
functions. Domain errors evaluate to NaN; renderers break curves at non-finite points.
Both implementations are checked against shared/fixtures/expressions.json.
"""
from __future__ import annotations

import math
import re

MAX_LENGTH = 160
MAX_DEPTH = 24
FUNCTIONS = {
    "sin": (1, math.sin), "cos": (1, math.cos), "tan": (1, math.tan), "asin": (1, math.asin),
    "acos": (1, math.acos), "atan": (1, math.atan), "exp": (1, math.exp), "ln": (1, math.log),
    "sqrt": (1, math.sqrt), "abs": (1, abs), "floor": (1, math.floor), "min": (2, min), "max": (2, max),
}
CONSTANTS = {"pi": math.pi, "e": math.e}
TOKEN = re.compile(r"\s*(?:(\d+\.?\d*|\.\d+)|([a-z]+)|(.))")


class ExpressionError(ValueError):
    pass


def _tokens(source):
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_LENGTH:
        raise ExpressionError(f"Expressions must be 1-{MAX_LENGTH} characters")
    tokens = []
    for number, name, symbol in TOKEN.findall(source):
        if number:
            tokens.append(("num", float(number)))
        elif name:
            tokens.append(("name", name))
        elif symbol in "+-*/^(),":
            tokens.append(("op", symbol))
        elif symbol.strip():
            raise ExpressionError(f"Unexpected character {symbol!r}")
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens, self.index, self.depth = tokens, 0, 0

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else (None, None)

    def take(self, value=None):
        token = self.peek()
        if token[0] is None or (value is not None and token[1] != value):
            raise ExpressionError(f"Expected {value or 'more input'}")
        self.index += 1
        return token

    def nest(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionError("Expression is nested too deeply")

    def expr(self):
        self.nest()
        node = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            node = (self.take()[1], node, self.term())
        self.depth -= 1
        return node

    def term(self):
        node = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            node = (self.take()[1], node, self.unary())
        return node

    def unary(self):
        if self.peek() in (("op", "+"), ("op", "-")):
            self.nest()
            sign = self.take()[1]
            node = self.unary()
            self.depth -= 1
            return node if sign == "+" else ("neg", node)
        return self.power()

    def power(self):
        node = self.primary()
        if self.peek() == ("op", "^"):
            self.take()
            self.nest()
            node = ("^", node, self.unary())
            self.depth -= 1
        return node

    def primary(self):
        kind, value = self.take()
        if kind == "num":
            return ("num", value)
        if kind == "name":
            if value in FUNCTIONS:
                arity = FUNCTIONS[value][0]
                self.take("(")
                args = [self.expr()]
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.expr())
                self.take(")")
                if len(args) != arity:
                    raise ExpressionError(f"{value} takes {arity} argument(s)")
                return ("call", value, args)
            if value in CONSTANTS:
                return ("num", CONSTANTS[value])
            if len(value) == 1:
                return ("var", value)
            raise ExpressionError(f"Unknown name {value!r}")
        if value == "(":
            node = self.expr()
            self.take(")")
            return node
        raise ExpressionError(f"Unexpected {value!r}")


def parse(source):
    parser = _Parser(_tokens(source))
    node = parser.expr()
    if parser.index != len(parser.tokens):
        raise ExpressionError("Unexpected trailing input")
    return node


def variables(node, found=None):
    found = set() if found is None else found
    if node[0] == "var":
        found.add(node[1])
    elif node[0] == "call":
        for arg in node[2]:
            variables(arg, found)
    elif node[0] != "num":
        for child in node[1:]:
            variables(child, found)
    return found


def _div(a, b):
    if b == 0:
        return math.nan if a == 0 or math.isnan(a) else math.copysign(math.inf, a) * math.copysign(1, b)
    return a / b


def evaluate(node, scope):
    try:
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "var":
            return float(scope.get(node[1], math.nan))
        if kind == "neg":
            return -evaluate(node[1], scope)
        if kind == "call":
            return float(FUNCTIONS[node[1]][1](*(evaluate(arg, scope) for arg in node[2])))
        a, b = evaluate(node[1], scope), evaluate(node[2], scope)
        if kind == "+":
            return a + b
        if kind == "-":
            return a - b
        if kind == "*":
            return a * b
        if kind == "/":
            return _div(a, b)
        return math.pow(a, b)
    except (ValueError, ZeroDivisionError):
        return math.nan
    except OverflowError:
        return math.inf


def compile_expression(source):
    """Parse once; returns (evaluate(scope) -> float, variables)."""
    node = parse(source)
    return (lambda scope: evaluate(node, scope)), variables(node)
