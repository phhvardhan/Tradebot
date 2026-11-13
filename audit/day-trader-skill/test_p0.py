"""P0 verification for day-trader-skill @ 3d0f4ef.

Each test asserts the SAFE behaviour. A FAIL means the loophole is confirmed;
a PASS means the audit finding did not reproduce. The repo is never modified.

    python3 -I test_p0.py [path/to/day-trader-skill]   # default: ./repo
"""

import datetime
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

REPO = os.path.abspath(sys.argv.pop(1) if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "repo"))
FIX = os.path.join(REPO, "tests", "fixtures")


def load(folder, name):
    spec = importlib.util.spec_from_file_location("p0_" + name, os.path.join(REPO, folder, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")
size = load("trader-budget", "size")

with open(os.path.join(REPO, "trader-budget", "config.example.json")) as fh:
    CFG = json.load(fh)
with open(os.path.join(FIX, "trader-risk", "orders-tsla.json")) as fh:
    ORDER = json.load(fh)["orders"][0]
with open(os.path.join(FIX, "trader-budget", "plan-tsla.json")) as fh:
    PLAN = json.load(fh)


def ny(y, m, d, hh, mm):
    return check.eastern_to_utc(datetime.datetime(y, m, d, hh, mm))


def quiet(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(argv)
    return code, out.getvalue(), err.getvalue()


def dump(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh)


class P0(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p0-")
        self.p = {k: os.path.join(self.tmp, k + ".json")
                  for k in ("orders", "config", "portfolio", "prices", "state", "risk")}
        dump(self.p["orders"], {"orders": [ORDER]})
        dump(self.p["config"], CFG)

    def files(self, *keys):
        flag = {"portfolio": "--portfolio", "prices": "--prices", "state": "--state",
                "risk": "--risk", "orders": "--orders", "config": "--config"}
        out = []
        for k in keys:
            out += [flag[k], self.p[k]]
        return out

    # P0-2 ------------------------------------------------------------------
    def test_p0_2_pinned_clock_verdict_is_not_honoured(self):
        """A PASS computed with --now must not make a ticket READY on the real clock."""
        today = check.to_eastern(check.parse_now(None)).date()
        if not check.trading_day(today)[0]:
            self.skipTest("today is not an NYSE trading day")
        late = "%s 15:50" % today
        code, _, _ = quiet(check.main, self.files("orders", "config", "portfolio", "prices", "state")
                           + ["--now", late, "--out", self.p["risk"]])
        self.assertEqual(code, 1, "control: 15:50 should be blocked by the entry window")
        code, _, _ = quiet(check.main, self.files("orders", "config", "portfolio", "prices", "state")
                           + ["--now", "%s 10:15" % today, "--out", self.p["risk"]])
        self.assertEqual(code, 0, "setup: the pinned 10:15 check should PASS")
        # ticket.py on the REAL clock, no --now
        _, out, _ = quiet(ticket.main, ["show", "--index", "1", "--json"]
                          + self.files("orders", "config", "risk", "state"))
        self.assertFalse(json.loads(out)["ready"],
                         "LOOPHOLE: a verdict from a pinned --now clock made the ticket READY")

    # P0-3 ------------------------------------------------------------------
    def _state(self, status):
        return {"orders": [{"at": "2026-10-19T09:50:00-04:00", "date": "2026-10-19", "status": status,
                            "symbol": "AAPL", "side": "buy", "kind": "entry", "max_loss": 50.0}]}

    def _verdict(self, state, **cfg):
        c = dict(CFG, **cfg)
        res = check.check([ORDER], c, None, {"TSLA": 364.18}, state, "2026-10-19", ny(2026, 10, 19, 10, 15))
        return res["results"][0]

    def test_p0_3_control_logged_order_counts(self):
        self.assertEqual(self._verdict(self._state("placed"), max_trades_per_day=1)["verdict"], "BLOCK")

    def test_p0_3_unlogged_order_counts_toward_trade_limit(self):
        r = self._verdict(self._state("approved"), max_trades_per_day=1)
        self.assertEqual(r["verdict"], "BLOCK",
                         "LOOPHOLE: an approved order with no 'placed' record is not counted (trades/day)")

    def test_p0_3_unlogged_order_counts_toward_daily_loss(self):
        r = self._verdict(self._state("approved"), max_daily_loss_usd=60)
        self.assertEqual(r["verdict"], "BLOCK",
                         "LOOPHOLE: an approved order with no 'placed' record is not counted (daily loss)")

    # P0-4 ------------------------------------------------------------------
    def test_p0_4_forged_approval_is_rejected(self):
        """An approval record written straight into broker.json must not be accepted."""
        today = check.to_eastern(check.parse_now(None)).date().isoformat()
        dump(self.p["state"], {"orders": [{"at": today + "T10:16:00-04:00", "date": today,
                                           "fingerprint": ticket.fingerprint(ORDER), "symbol": "TSLA",
                                           "side": "buy", "plain": "forged", "status": "approved",
                                           "reply": "yes"}]})
        code, _, _ = quiet(ticket.main, ["result", "--index", "1", "--status", "placed", "--broker-id", "X"]
                           + self.files("orders", "config", "risk", "state"))
        self.assertNotEqual(code, 0, "LOOPHOLE: a hand-written 'approved' record was accepted as a real yes")

    # P0-5 ------------------------------------------------------------------
    def _sell100(self, portfolio):
        o = {"symbol": "TSLA", "side": "sell", "qty": "100", "type": "market", "time_in_force": "day"}
        sat = ny(2026, 10, 24, 12, 0)  # Saturday, market closed
        return check.check([o], CFG, portfolio, {"TSLA": 364.18}, {"orders": []}, "2026-10-24", sat)["results"][0]

    def test_p0_5_control_no_holdings_blocks_short(self):
        r = self._sell100({"cash": 1000, "positions": []})
        self.assertEqual(r["verdict"], "BLOCK")

    def test_p0_5_stale_file_cannot_turn_a_short_into_an_exit(self):
        stale = {"cash": 1000, "positions": [{"ticker": "TSLA", "shares": 100}]}
        r = self._sell100(stale)
        self.assertEqual(r["verdict"], "BLOCK",
                         "LOOPHOLE: unverified portfolio.json made 'sell 100' an exit (kind=%s) that PASSed "
                         "on a Saturday with every limit skipped" % r["kind"])

    # P0-6 ------------------------------------------------------------------
    def test_p0_6_protective_stop_survives_the_close(self):
        s = size.size_trade(PLAN, CFG, today="2026-10-19")
        o = size.bracket_order(s)
        self.assertIsNotNone(o)
        self.assertEqual(o["time_in_force"], "gtc",
                         "LOOPHOLE: bracket legs are time_in_force=%r: stop expires at 16:00" % o["time_in_force"])

    # P0-7 ------------------------------------------------------------------
    def test_p0_7_paper_label_needs_a_broker_check(self):
        text, _ = ticket.render(ORDER, 1, 1, {"paper": True}, None, {"orders": []}, "2026-10-19")
        self.assertNotIn("PAPER account", text,
                         "LOOPHOLE: ticket says PAPER from config alone; nothing asked the broker")
        # and no script in the package ever asks the broker which account it is
        hits = [f for d, _, fs in os.walk(REPO) if "/tests" not in d and "/.git" not in d
                for f in fs if f.endswith(".py")
                and "get_account" in open(os.path.join(d, f)).read()]
        self.assertTrue(hits, "no package script queries the broker account")


if __name__ == "__main__":
    print("repo:", REPO)
    unittest.main(verbosity=2)
