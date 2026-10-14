"""Whole-tree guards for the five bug classes of the Trade R5 class sweep, over the code THIS repo owns for Trade
(app/backend, batch/src, demo). Mirror of Trade's `tests/test_class_sweep_guards.py` (D306: the same five shapes, the same allow-list rule),
self-contained because this repo must not import Trade's tests. An allow-list entry is `file: (count, one-line
reason)`: a NEW site in a listed file fails, a listed file that no longer has its sites fails (trim the entry in the
same change), a new site elsewhere fails with the helper to use. Triage record: Trade backlog item
`2026-10-14-r5-class-sweep-submodules`.

1. WALL CLOCK      host-local `date.today()` / naive `datetime.now()` / `utcnow()` / naive `fromtimestamp`
2. UTC-DAY KEY     `datetime.now(utc).date()` as a key (a market day is the IST day for India)
3. DURABLE WRITES  a bare `.write_text` / `.write_bytes` / `open(..., "w")` on a final path: write a unique temp file in the same directory, fsync, then os.replace
4. SECRETS         a credential in a URL query string, or an HTTP error body echoed into an exception/log unredacted
5. UNBOUNDED CARRY `ffill()` without limit, any `bfill`, `merge_asof` without tolerance, `rolling(center=True)`, negative `shift`
6. HOST / PATH     a quoted `~/.vibe-trading` / `/Users/` / `0.0.0.0` literal or a `host:port` literal of a tier service
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: Files and directories this repo owns for Trade. Add a new Trade-owned directory here.
SCOPE = ('app/backend', 'batch/src', 'demo')
_SKIP = ("/tests/", "/test/", "node_modules", "/.venv", "/frontend/")


def _files() -> list[Path]:
    out: list[Path] = []
    for entry in SCOPE:
        p = ROOT / entry
        if p.is_file():
            out.append(p)
        else:
            assert p.is_dir(), f"SCOPE entry {entry} does not exist: update the list"
            out += sorted(p.rglob("*.py"))
    return [f for f in out if not any(s in "/" + f.relative_to(ROOT).as_posix() for s in _SKIP) and not f.name.startswith("test_") and f.name != "conftest.py"]


def _scan(extract) -> dict[str, list]:
    found: dict[str, list] = {}
    for path in _files():
        sites = extract(ast.parse(path.read_text(encoding="utf-8")))
        if sites:
            found[path.relative_to(ROOT).as_posix()] = sites
    return found


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _check(found: dict[str, list], allow: dict[str, tuple[int, str]], what: str) -> None:
    problems = []
    for rel, sites in sorted(found.items()):
        if rel not in allow:
            problems.append(f"NEW  {rel}: {what} at line(s) {[s[0] for s in sites]}")
        elif len(sites) != allow[rel][0]:
            problems.append(f"{'MORE' if len(sites) > allow[rel][0] else 'FEWER'} {rel}: {len(sites)} site(s), allow-list says {allow[rel][0]} ({allow[rel][1]}); lines {[s[0] for s in sites]}")
    for rel in sorted(set(allow) - set(found)):
        problems.append(f"STALE {rel}: allow-list entry with no site left; delete it")
    assert not problems, "\n".join(problems)


def clock_sites(tree: ast.AST) -> list:
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = _dotted(n.func)
        bare = not (n.args or n.keywords)
        if f.endswith("date.today") or f.endswith("datetime.today") or f.endswith("datetime.utcnow"):
            out.append([n.lineno, f])
        elif (f.endswith("datetime.now") or f.endswith("Timestamp.now")) and bare:
            out.append([n.lineno, f])
        elif f.endswith("fromtimestamp") and len(n.args) + len(n.keywords) < 2:
            out.append([n.lineno, f])
    return out


def utcday_sites(tree: ast.AST) -> list:
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "date" and not n.args:
            inner = n.func.value
            if isinstance(inner, ast.Call) and _dotted(inner.func).endswith(".now") and inner.args and _dotted(inner.args[0]).lower().endswith("utc"):
                out.append([n.lineno, "utc-day"])
    return out


_SCRATCH = re.compile(r"tmp|temp", re.IGNORECASE)


def write_sites(tree: ast.AST) -> list:
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = _dotted(n.func)
        if isinstance(n.func, ast.Attribute) and n.func.attr in ("write_text", "write_bytes"):
            if not _SCRATCH.search(ast.unparse(n.func.value)):
                out.append([n.lineno, n.func.attr])
        elif f == "open" or f.endswith(".open"):
            mode = None
            if f == "open" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant):
                mode = n.args[1].value
            if f.endswith(".open") and n.args and isinstance(n.args[0], ast.Constant):
                mode = n.args[0].value
            for k in n.keywords:
                if k.arg == "mode" and isinstance(k.value, ast.Constant):
                    mode = k.value.value
            if isinstance(mode, str) and ("w" in mode or "x" in mode):
                recv = ast.unparse(n.func.value) if isinstance(n.func, ast.Attribute) else (ast.unparse(n.args[0]) if n.args else "")
                if not _SCRATCH.search(recv):
                    out.append([n.lineno, "open-w"])
    return out


_CRED_QUERY = re.compile(r"[?&](?:api_?key|api_?token|apikey|token|access_token|key|secret|password)=$", re.IGNORECASE)
_LOGGERS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _calls_redact(node: ast.AST) -> bool:
    return any(isinstance(c, ast.Call) and "redact" in _dotted(c.func) for c in ast.walk(node))


def secret_sites(tree: ast.AST) -> list:
    out = []
    parents = {id(c): p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            for left, right in zip(n.values, n.values[1:]):
                if isinstance(left, ast.Constant) and isinstance(left.value, str) and isinstance(right, ast.FormattedValue) and _CRED_QUERY.search(left.value):
                    out.append([n.lineno, "url-query-credential"])
        if isinstance(n, ast.Attribute) and n.attr in ("text", "content") and isinstance(n.value, ast.Name) and n.value.id in {"r", "resp", "response", "res"}:
            cur: ast.AST | None = n
            sink = False
            while cur is not None and not isinstance(cur, (ast.stmt,)):
                cur = parents.get(id(cur))
                if isinstance(cur, ast.Call):
                    name = _dotted(cur.func)
                    if name.endswith("Error") or name.endswith("Exception") or name.split(".")[-1] in _LOGGERS:
                        sink = not _calls_redact(cur)
                        break
            if sink:
                out.append([n.lineno, "unredacted-http-body"])
    return out


def fill_sites(tree: ast.AST) -> list:
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        attr, kws = n.func.attr, {k.arg for k in n.keywords}
        if attr in ("ffill", "pad") and "limit" not in kws and not (n.args and len(n.args) >= 2):
            out.append([n.lineno, "ffill-unbounded"])
        elif attr in ("bfill", "backfill"):
            out.append([n.lineno, "bfill"])
        elif attr == "fillna" and any(k.arg == "method" for k in n.keywords):
            out.append([n.lineno, "fillna-method"])
        elif _dotted(n.func).endswith("merge_asof") and "tolerance" not in kws:
            out.append([n.lineno, "merge_asof-no-tolerance"])
        elif attr == "rolling" and any(k.arg == "center" and isinstance(k.value, ast.Constant) and k.value.value is True for k in n.keywords):
            out.append([n.lineno, "rolling-center"])
        elif attr == "shift" and n.args and isinstance(n.args[0], ast.UnaryOp) and isinstance(n.args[0].op, ast.USub):
            out.append([n.lineno, "shift-negative"])
        elif attr == "shift" and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, int) and n.args[0].value < 0:
            out.append([n.lineno, "shift-negative"])
    return out


_HOSTPORT = re.compile(r"(?:127\.0\.0\.1|localhost):(\d{3,5})\b")
_PATHLIT = re.compile(r"\.vibe-trading|/Users/|(?<![\d.])0\.0\.0\.0(?![\d.])")


def hostpath_sites(tree: ast.AST) -> list:
    docstrings = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
            for m in _HOSTPORT.finditer(n.value):
                out.append([n.lineno, f"hostport:{m.group(1)}"])
            if _PATHLIT.search(n.value) and "Chrome/" not in n.value:
                out.append([n.lineno, "path-literal"])
    return out


CLOCK_ALLOWED: dict[str, tuple[int, str]] = {}
UTCDAY_ALLOWED: dict[str, tuple[int, str]] = {}
WRITES_ALLOWED: dict[str, tuple[int, str]] = {'demo/demo_walkthrough/align-cover-vocal.py': (2, 'demo script run by a person: its output is a media/report file they review'),
 'demo/demo_walkthrough/generate-cover-vocal.py': (1, 'demo script run by a person: its output is a media/report file they review')}
SECRETS_ALLOWED: dict[str, tuple[int, str]] = {}
FILL_ALLOWED: dict[str, tuple[int, str]] = {}
HOSTPATH_ALLOWED: dict[str, tuple[int, str]] = {}


def test_no_host_local_clock_read_outside_the_allow_list() -> None:
    _check(_scan(clock_sites), CLOCK_ALLOWED, "host-local clock read: a market day is `market_registry.market_today(market)` (IST for India)")


def test_no_utc_day_key_outside_the_allow_list() -> None:
    _check(_scan(utcday_sites), UTCDAY_ALLOWED, "UTC-day key: a market-data key is the market day, not the UTC date")


def test_no_bare_write_to_a_durable_path() -> None:
    _check(_scan(write_sites), WRITES_ALLOWED, "bare write: write a unique temp file in the same directory, fsync, then os.replace")


def test_no_credential_in_a_url_or_unredacted_error_body() -> None:
    _check(_scan(secret_sites), SECRETS_ALLOWED, "credential leak shape: redact the body / keep the key out of the URL")


def test_no_unbounded_carry_or_lookahead() -> None:
    _check(_scan(fill_sites), FILL_ALLOWED, "unbounded fill / look-ahead: give ffill a limit or label it")


def test_no_host_or_path_literal_outside_the_allow_list() -> None:
    _check(_scan(hostpath_sites), HOSTPATH_ALLOWED, "host/path literal: tier ports come from stack/ports.yaml, runtime roots from the runtime root")


def test_detector_sees_the_shapes_it_exists_for() -> None:
    assert [s[1] for s in clock_sites(ast.parse("date.today()\ndatetime.now()\ndatetime.now(tz)\ndatetime.utcnow()\n"))] == ["date.today", "datetime.now", "datetime.utcnow"]
    assert [s[1] for s in utcday_sites(ast.parse("datetime.now(timezone.utc).date()\ndatetime.now(IST).date()\n"))] == ["utc-day"]
    assert write_sites(ast.parse("p.write_text(x)\ntmp.write_text(x)\nopen(p, 'w')\nopen(p)\nopen(p, 'a')\n")) == [[1, "write_text"], [3, "open-w"]]
    leak = "raise RuntimeError(f'x {r.text[:3]}')\nraise RuntimeError(f'x {redact_text(r.text[:3])}')\nu = f'http://h/s?token={t}'\n"
    assert sorted(s[1] for s in secret_sites(ast.parse(leak))) == ["unredacted-http-body", "url-query-credential"]
    assert [s[1] for s in fill_sites(ast.parse("df.ffill()\ndf.bfill()\ns.shift(-3)\ndf.ffill(limit=5)\n"))] == ["ffill-unbounded", "bfill", "shift-negative"]
    assert [s[1] for s in hostpath_sites(ast.parse("u = 'http://127.0.0.1:5001/x'\np = '~/.vibe-trading'\n"))] == ["hostport:5001", "path-literal"]
