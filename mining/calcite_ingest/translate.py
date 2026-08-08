"""
Stage B of the Calcite rule-ingestion pilot (docs/rule_ingestion_scope.md, I0).

Translate both sides of a checkSimplify pair into SQL that provensql's error
engine can parse:

  * input side  -- Calcite test DSL:  plus(vInt(0), nullInt), div(a, one), ...
  * expected side -- RexNode dump:     +(?0.int0, 1), null:INTEGER, IS NULL(...)

Anything outside the modeled arithmetic / NULL / IS [NOT] NULL / CAST fragment
returns None, so the runner can *count* it as an honest skip rather than guess.
Type and nullability are carried in the column name (vIntNotNull -> notNullInt0,
dumped as ?0.notNullInt0); the error engine reads not-null from that prefix.

Faithfulness matters more than coverage here: a wrong translation would fabricate
or hide a divergence. We translate a conservative subset and skip the rest.
"""

import re

# ---- shared tiny s-expression tokenizer for HEAD(arg, arg, ...) forms --------

_TOK = re.compile(r"""\s*(?:(?P<op><=|>=|<>|[-+*/(),:])|(?P<word>[^\s(),:]+))""")


def _tokenize(s):
    toks, i = [], 0
    while i < len(s):
        m = _TOK.match(s, i)
        if not m or m.end() == i:
            break
        i = m.end()
        toks.append(m.group("op") or m.group("word"))
    return toks


class _P:
    """Recursive-descent parser over the token stream; dispatches on notation."""

    def __init__(self, toks, dump):
        self.t, self.i, self.dump = toks, 0, dump

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.t[self.i]; self.i += 1; return tok

    def parse(self):
        return self.expr() if self.dump else self.dsl()

    # ---- expected side: RexNode dump ----------------------------------------
    def expr(self):
        tok = self.next()
        # prefix symbol operators: +(a,b), *(a,b), IS NULL(x), CAST(x):T
        val = self._call(tok, dump=True) if self.peek() == "(" else self._atom_dump(tok)
        self._maybe_type()                           # consume trailing :TYPE, if any
        return val

    def _maybe_type(self):
        # RexNode dumps append ':TYPE' to typed atoms/casts: null:INTEGER,
        # CAST(x):BIGINT, 1:DECIMAL(19, 0), x:INTEGER NOT NULL. Consume and drop.
        if self.peek() != ":":
            return
        self.next()                                  # ':'
        if self.peek() is not None:
            self.next()                              # base type word
        if self.peek() == "(":                       # DECIMAL(19, 0)
            depth = 0
            while self.peek() is not None:
                t = self.next()
                if t == "(":
                    depth += 1
                elif t == ")":
                    depth -= 1
                    if depth == 0:
                        break
        while self.peek() in ("NOT", "NULL"):        # NOT NULL suffix
            self.next()

    def _atom_dump(self, tok):
        if tok.startswith("?"):                      # ?0.int0 -> int0
            return tok.split(".", 1)[1]
        if tok == "null":
            return "NULL"
        if tok in ("true", "false"):
            return tok.upper()
        if re.fullmatch(r"-?\d+(\.\d+)?", tok):
            return tok
        return None

    # ---- input side: Calcite test DSL ---------------------------------------
    def dsl(self):
        tok = self.next()
        if self.peek() == "(":
            return self._call(tok, dump=False)
        return self._atom_dsl(tok)

    def _atom_dsl(self, tok):
        m = re.fullmatch(r"v(Int|Bigint|BigInt|Long|Short|Smallint|Decimal|Double|Float)", tok)
        if m:  # a bare vInt with no following () shouldn't happen, but be safe
            return None
        if tok in ("nullInt", "nullBigInt", "nullDecimal", "nullDouble", "nullLong"):
            return "NULL"
        if tok in ("trueLiteral", "true"):
            return "TRUE"
        if tok in ("falseLiteral", "false"):
            return "FALSE"
        if re.fullmatch(r"-?\d+(\.\d+)?", tok):
            return tok
        return None  # unknown identifier / local ref we do not resolve

    # ---- shared call handling -----------------------------------------------
    def _args(self):
        assert self.next() == "("
        args = []
        if self.peek() == ")":
            self.next(); return args
        while True:
            a = self.expr() if self.dump else self.dsl()
            args.append(a)
            sep = self.peek()
            if sep == ",":
                self.next(); continue
            if sep == ")":
                self.next(); break
            return None  # malformed
        return args

    def _call(self, head, dump):
        # v-builders with an index arg: vInt(0) -> int0, vIntNotNull(1) -> notNullInt1
        vm = re.fullmatch(r"v(Int|Bigint|BigInt|Long|Short|Smallint|Decimal|Double|Float)(NotNull)?", head)
        if vm and not dump:
            base = {"Int": "int", "Bigint": "bigint", "BigInt": "bigint", "Long": "bigint",
                    "Short": "short", "Smallint": "short", "Decimal": "decimal",
                    "Double": "double", "Float": "float"}[vm.group(1)]
            args = self._args()
            if args is None:
                return None
            idx = args[0] if args and re.fullmatch(r"\d+", str(args[0])) else "0"
            prefix = "notNull" + base[0].upper() + base[1:] if vm.group(2) else base
            return f"{prefix}{idx}"
        if head == "literal":
            return self._literal_arg()
        if head.lower() == "cast":
            # cast(expr, type): translate the value, ignore the type constructor
            # (which may be an unmodeled helper like tBigInt()). Outcome-transparent.
            args = self._args()
            return args[0] if args and args[0] is not None else None
        args = self._args()
        if args is None or any(a is None for a in args):
            return None
        return self._emit(head, args)

    def _literal_arg(self):
        # literal(1), literal(BigDecimal.valueOf(2.5)), literal(BigDecimal.ZERO)
        assert self.next() == "("
        depth, buf = 1, []
        while depth:
            tok = self.next()
            if tok == "(":
                depth += 1
            elif tok == ")":
                depth -= 1
                if depth == 0:
                    break
            buf.append(tok)
        raw = "".join(buf)
        if "ZERO" in raw:
            return "0"
        if "ONE" in raw:
            return "1"
        m = re.search(r"-?\d+(\.\d+)?", raw)
        return m.group(0) if m else None

    # operator name -> SQL emitter; anything not here -> out of fragment (None)
    def _emit(self, head, a):
        h = head.upper()
        bin_arith = {"PLUS": "+", "+": "+", "MINUS": "-", "-": "-",
                     "MUL": "*", "*": "*", "DIV": "/", "/": "/", "SUB": "-"}
        if h in bin_arith and len(a) == 2:
            return f"({a[0]} {bin_arith[h]} {a[1]})"
        if h in ("ISNULL", "IS NULL") and len(a) == 1:
            return f"({a[0]}) IS NULL"
        if h in ("ISNOTNULL", "IS NOT NULL") and len(a) == 1:
            return f"({a[0]}) IS NOT NULL"
        if h in ("NEG",) and len(a) == 1:
            return f"(-{a[0]})"
        if h == "NULLIF" and len(a) == 2:
            return f"NULLIF({a[0]}, {a[1]})"
        if h == "CAST" and len(a) >= 1:
            return f"{a[0]}"  # passthrough; engine treats cast as outcome-transparent
        return None  # AND/OR/NOT/CASE/COALESCE/comparisons -> not in fragment


def translate_dsl(text):
    try:
        p = _P(_tokenize(text), dump=False)
        out = p.parse()
        return out if (out is not None and p.peek() is None) else None
    except Exception:
        return None


def translate_dump(text):
    # RexNode dumps write 'IS NULL(' / 'IS NOT NULL(' / 'CAST(' with spaces; the
    # tokenizer splits on spaces, so pre-join those multiword heads.
    text = (text.replace("IS NOT NULL(", "ISNOTNULL(")
                .replace("IS NULL(", "ISNULL("))
    try:
        p = _P(_tokenize(text), dump=True)
        out = p.parse()
        return out if (out is not None and p.peek() is None) else None
    except Exception:
        return None
