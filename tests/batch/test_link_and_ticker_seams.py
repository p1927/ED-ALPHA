from __future__ import annotations

from link_gdelt_gkg_companies import find_matching_ciks
import fetch_company_tickers as fct


def _index(*titles):
    pairs = sorted((t, i) for i, t in enumerate(titles))
    return [t for t, _ in pairs], [i for _, i in pairs]


def test_company_match_is_not_a_bare_prefix():
    titles, ciks = _index("apple inc.", "apple hospitality reit, inc.", "ford motor co", "ford motor credit co llc")
    got = {titles[ciks.index(c)] for c in find_matching_ciks("apple", titles, ciks)}
    assert got == {"apple inc."}
    got = {titles[ciks.index(c)] for c in find_matching_ciks("ford motor", titles, ciks)}
    assert got == {"ford motor co"}
    got = {titles[ciks.index(c)] for c in find_matching_ciks("ford motor co", titles, ciks)}
    assert got == {"ford motor co"}


class _Cursor:
    def __init__(self):
        self.executed, self.rowcount = [], 3

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class _Conn:
    def __init__(self):
        self.cursor_obj, self.commits = _Cursor(), 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


def test_stale_ticker_pairs_are_pruned_only_from_a_sane_feed():
    conn = _Conn()
    assert fct.delete_stale_company_tickers(conn, [(1, "A")] * 10) == 0  # truncated feed never prunes
    assert conn.cursor_obj.executed == []
    pairs = [(i, f"T{i}") for i in range(fct.MIN_FEED_PAIRS_FOR_PRUNE)]
    assert fct.delete_stale_company_tickers(conn, pairs) == 3
    sql, params = conn.cursor_obj.executed[0]
    assert "DELETE FROM company_tickers" in sql and params[0][0] == 0 and params[1][0] == "T0"
    assert conn.commits == 1
