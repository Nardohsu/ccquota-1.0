#!/usr/bin/env python3
"""Tests for the parts of ccquota that are easy to get wrong.

Run with:  python -m unittest test_ccquota -v
"""
import importlib
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone

import ccquota as q


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def line(msg_id, req_id, epoch, out=100, cache_read=50000, cache_create=0, inp=1):
    return json.dumps({
        "type": "assistant",
        "timestamp": iso(epoch),
        "requestId": req_id,
        "message": {
            "id": msg_id,
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": cache_create,
                "cache_read_input_tokens": cache_read,
            },
        },
    }) + "\n"


class TempConfig(unittest.TestCase):
    """Each test gets an isolated CLAUDE_CONFIG_DIR."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccquota-test-")
        self.transcripts = os.path.join(self.dir, "projects", "proj")
        os.makedirs(self.transcripts)
        self._prev = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.dir

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._prev
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, text, mode="w"):
        p = os.path.join(self.transcripts, name)
        with open(p, mode, encoding="utf-8") as fh:
            fh.write(text)
        return p


class TestBilling(unittest.TestCase):
    def test_excludes_cache_reads(self):
        usage = {"input_tokens": 5, "output_tokens": 10,
                 "cache_creation_input_tokens": 20,
                 "cache_read_input_tokens": 1000000}
        self.assertEqual(q.bill(usage), 35)

    def test_missing_fields_default_to_zero(self):
        self.assertEqual(q.bill({}), 0)


class TestDedup(TempConfig):
    def test_repeated_message_id_counted_once(self):
        """One response written as several lines must not be summed."""
        now = time.time()
        self.write("a.jsonl", "".join(
            line("msg_1", "req_1", now - 60, out=300) for _ in range(20)))
        entries = q.collect(now)
        self.assertEqual(len(entries), 1)
        self.assertEqual(sum(v[1] for v in entries.values()), 301)

    def test_streamed_growth_keeps_final_value(self):
        """A streamed response is rewritten as it grows; keep the largest."""
        now = time.time()
        self.write("a.jsonl",
                   line("msg_1", "req_1", now - 60, out=100)
                   + line("msg_1", "req_1", now - 59, out=250)
                   + line("msg_1", "req_1", now - 58, out=180))
        entries = q.collect(now)
        self.assertEqual(sum(v[1] for v in entries.values()), 251)

    def test_distinct_responses_are_summed(self):
        now = time.time()
        self.write("a.jsonl",
                   line("msg_1", "req_1", now - 60, out=100)
                   + line("msg_2", "req_2", now - 50, out=200))
        self.assertEqual(sum(v[1] for v in q.collect(now).values()), 302)

    def test_dedup_reaches_across_files(self):
        now = time.time()
        self.write("a.jsonl", line("msg_1", "req_1", now - 60, out=100))
        self.write("b.jsonl", line("msg_1", "req_1", now - 60, out=100))
        self.assertEqual(len(q.collect(now)), 1)


class TestIncrementalCache(TempConfig):
    def test_appended_lines_are_picked_up(self):
        now = time.time()
        self.write("a.jsonl", line("msg_1", "req_1", now - 120, out=100))
        self.assertEqual(sum(v[1] for v in q.collect(now).values()), 101)

        self.write("a.jsonl", line("msg_2", "req_2", now - 60, out=200), mode="a")
        self.assertEqual(sum(v[1] for v in q.collect(now).values()), 302)

    def test_torn_final_line_is_resumed_once_complete(self):
        """A half-written line must be skipped, then read on a later run."""
        now = time.time()
        good = line("msg_1", "req_1", now - 120, out=100)
        whole = line("msg_2", "req_2", now - 60, out=200)
        self.write("a.jsonl", good + whole[:-10])
        self.assertEqual(sum(v[1] for v in q.collect(now).values()), 101)

        self.write("a.jsonl", good + whole, mode="w")
        self.assertEqual(sum(v[1] for v in q.collect(now).values()), 302)

    def test_inplace_rewrite_of_equal_length_is_detected(self):
        """Two transcripts can be byte-identical in length; size alone lies."""
        now = time.time()
        first = line("msg_1", "req_1", now - 120, out=100)
        second = line("msg_2", "req_2", now - 60, out=200)
        self.assertEqual(len(first), len(second))       # guards the premise
        self.write("a.jsonl", first)
        self.assertEqual(sum(v[1] for v in q.collect(now).values()), 101)

        time.sleep(0.01)                                 # ensure mtime moves
        self.write("a.jsonl", second, mode="w")
        self.assertEqual(sum(v[1] for v in q.collect(now).values()), 201)

    def test_truncated_file_triggers_reparse(self):
        now = time.time()
        self.write("a.jsonl", "".join(
            line("msg_%d" % i, "req_%d" % i, now - 100 + i, out=100)
            for i in range(10)))
        first = sum(v[1] for v in q.collect(now).values())
        self.assertEqual(first, 1010)
        self.write("a.jsonl", line("msg_z", "req_z", now - 30, out=5))
        self.assertEqual(sum(v[1] for v in q.collect(now).values()), 6)


class TestFiveHourBlock(unittest.TestCase):
    def entries(self, *offsets_from_now, **kw):
        now = kw.get("now", time.time())
        return {"k%d" % i: [int((now + off) // 60), 100, 0]
                for i, off in enumerate(offsets_from_now)}

    def test_gap_opens_a_new_window(self):
        now = time.time()
        # activity 8h ago, then again 30m ago -> window anchored to the recent one
        e = self.entries(-8 * 3600, -1800, now=now)
        start, end = q.current_block(e, now)
        self.assertAlmostEqual(start, now - 1800, delta=60)
        self.assertAlmostEqual(end - start, q.FIVE_HOUR, delta=1)

    def test_continuous_activity_keeps_one_window(self):
        now = time.time()
        e = self.entries(-3600, -1800, -600, now=now)
        start, _end = q.current_block(e, now)
        self.assertAlmostEqual(start, now - 3600, delta=60)

    def test_window_is_not_snapped_to_the_hour(self):
        """Real reset times carry minutes, so the start must not be rounded."""
        base = datetime(2026, 8, 31, 19, 41, tzinfo=timezone.utc).timestamp()
        now = base + 600
        start, _end = q.current_block({"k": [int(base // 60), 100, 0]}, now)
        self.assertEqual(datetime.fromtimestamp(start, timezone.utc).minute, 41)

    def test_lapsed_window_reports_idle(self):
        now = time.time()
        self.assertIsNone(q.current_block(self.entries(-6 * 3600, now=now), now))

    def test_no_activity_reports_idle(self):
        self.assertIsNone(q.current_block({}, time.time()))


class TestRollForward(unittest.TestCase):
    def test_stale_weekly_rolls_into_the_current_window(self):
        now = time.time()
        stale = now - 3 * q.WEEK - 3600          # three weeks and an hour ago
        start, end = q.roll_forward(stale, q.WEEK, now)
        self.assertLessEqual(start, now)
        self.assertGreater(end, now)
        self.assertAlmostEqual(end - start, q.WEEK, delta=1)

    def test_cadence_is_preserved(self):
        now = time.time()
        stale = now - 3 * q.WEEK - 3600
        _start, end = q.roll_forward(stale, q.WEEK, now)
        self.assertAlmostEqual((end - stale) % q.WEEK, 0, delta=1)

    def test_future_reset_is_left_alone(self):
        now = time.time()
        future = now + 3600
        start, end = q.roll_forward(future, q.WEEK, now)
        self.assertEqual(end, future)
        self.assertAlmostEqual(end - start, q.WEEK, delta=1)

    def test_missing_inputs_return_none(self):
        self.assertIsNone(q.roll_forward(None, q.WEEK, time.time()))
        self.assertIsNone(q.roll_forward(time.time(), None, time.time()))


class TestOfficialAnchor(TempConfig):
    def anchor(self, weekly_percent, weekly_resets_at, fetched_ms):
        payload = {"cachedUsageUtilization": {
            "fetchedAtMs": fetched_ms,
            "utilization": {"limits": [
                {"kind": "weekly_all", "group": "weekly",
                 "percent": weekly_percent, "resets_at": weekly_resets_at},
            ]},
        }}
        with open(os.path.join(self.dir, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_open_window_is_fresh(self):
        now = time.time()
        self.anchor(53, iso(now + 3600), int((now - 60) * 1000))
        self.assertTrue(q.official_limits(now)["weekly_all"]["fresh"])

    def test_expired_window_is_not_fresh(self):
        """A percent from a window that has already reset must not be shown."""
        now = time.time()
        self.anchor(53, iso(now - 3600), int((now - 8 * 86400) * 1000))
        limit = q.official_limits(now)["weekly_all"]
        self.assertFalse(limit["fresh"])
        self.assertEqual(limit["percent"], 53)   # still readable, just not current

    def test_missing_config_is_survivable(self):
        self.assertEqual(q.official_limits(time.time()), {})

    def test_decoy_config_does_not_win(self):
        """A second .claude.json without quota data must not shadow the real one."""
        now = time.time()
        decoy = {"firstStartTime": 1, "machineID": "abc"}
        with open(os.path.join(self.dir, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump(decoy, fh)
        real = {"cachedUsageUtilization": {
            "fetchedAtMs": int(now * 1000),
            "utilization": {"limits": [
                {"kind": "weekly_all", "group": "weekly",
                 "percent": 42, "resets_at": iso(now + 3600)}]}}}
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(real, fh)

        self.assertTrue(q.claude_json_path().endswith("config.json"))
        self.assertEqual(q.official_limits(now)["weekly_all"]["percent"], 42)


class TestWindowSum(unittest.TestCase):
    def test_boundaries_are_half_open(self):
        now = time.time()
        minute = int(now // 60)
        entries = {"a": [minute - 10, 100, 7], "b": [minute - 1, 200, 9]}
        tok, cache_read, count = q.window_sum(entries, (minute - 5) * 60, now)
        self.assertEqual((tok, cache_read, count), (200, 9, 1))


class TestFormatting(unittest.TestCase):
    def test_human(self):
        self.assertEqual(q.human(999), "999")
        self.assertEqual(q.human(1500), "2k")
        self.assertEqual(q.human(2_600_000), "2.6M")

    def test_dur(self):
        self.assertEqual(q.dur(59), "0m")
        self.assertEqual(q.dur(90 * 60), "1h30m")
        self.assertEqual(q.dur(50 * 3600), "2d2h")
        self.assertEqual(q.dur(-5), "0m")

    def test_level_thresholds(self):
        self.assertEqual(q.level(0.10), q.GRN)
        self.assertEqual(q.level(0.75), q.YEL)
        self.assertEqual(q.level(0.95), q.RED)


class TestPayload(TempConfig):
    """The host sends live figures on stdin; they outrank everything on disk."""

    def render(self, data, segments):
        os.environ["CCQUOTA_SEGMENTS"] = segments
        try:
            return q.build(data)
        finally:
            os.environ.pop("CCQUOTA_SEGMENTS", None)

    def payload(self, five=87, seven=12, now=None):
        now = now or time.time()
        return {"rate_limits": {
            "five_hour": {"used_percentage": five, "resets_at": int(now + 5400)},
            "seven_day": {"used_percentage": seven, "resets_at": int(now + 550000)}}}

    def test_extracts_a_limit(self):
        lim = q.payload_limit(self.payload(), "five_hour")
        self.assertEqual(lim["percent"], 87)

    def test_absent_limits_return_none(self):
        self.assertIsNone(q.payload_limit({}, "five_hour"))
        self.assertIsNone(q.payload_limit({"rate_limits": {}}, "five_hour"))
        self.assertIsNone(
            q.payload_limit({"rate_limits": {"five_hour": {}}}, "five_hour"))

    def test_live_figures_are_shown_without_the_estimate_marker(self):
        out = self.render(self.payload(), "5h,wk")
        self.assertIn("87%", out)
        self.assertIn("12%", out)
        self.assertNotIn("~", out)

    def test_live_figures_beat_the_on_disk_cache(self):
        """The cache only refreshes at startup; the payload is current."""
        now = time.time()
        stale = {"cachedUsageUtilization": {
            "fetchedAtMs": int(now * 1000),
            "utilization": {"limits": [
                {"kind": "session", "group": "session",
                 "percent": 3, "resets_at": iso(now + 3600)}]}}}
        with open(os.path.join(self.dir, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump(stale, fh)
        out = self.render(self.payload(five=87), "5h")
        self.assertIn("87%", out)
        self.assertNotIn("3%", out)

    def test_falls_back_to_the_estimate_without_a_payload(self):
        out = self.render({}, "wk")
        self.assertIn("~", out)

    def test_context_uses_the_hosts_window_size(self):
        out = self.render({"context_window": {"used_percentage": 5,
                                              "context_window_size": 1000000}}, "ctx")
        self.assertIn("5%", out)

    def test_cache_segment_flags_a_cold_cache(self):
        warm = self.render({"prompt_cache": {"hit_ratio": 0.68, "warm": True}}, "cache")
        cold = self.render({"prompt_cache": {"hit_ratio": 0.68, "warm": False}}, "cache")
        self.assertIn("68%", warm)
        self.assertNotIn("*", warm)
        self.assertIn("*", cold)

    def test_transcripts_are_not_scanned_when_the_payload_suffices(self):
        """Segments served entirely by the payload must not pay for a sweep."""
        calls = []
        original = q.collect
        q.collect = lambda now: calls.append(now) or {}
        try:
            self.render(self.payload(), "5h,wk,ctx,cache")
            self.assertEqual(calls, [])
            self.render(self.payload(), "5h,today")
            self.assertEqual(len(calls), 1)
        finally:
            q.collect = original


class TestContextSegment(TempConfig):
    def transcript(self, ctx_tokens):
        p = os.path.join(self.dir, "t.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(line("msg_1", "req_1", time.time() - 30,
                          inp=ctx_tokens, out=10, cache_read=0, cache_create=0))
        return p

    def render(self, ctx_tokens, limit=None):
        os.environ["CCQUOTA_SEGMENTS"] = "ctx"
        if limit is None:
            os.environ.pop("CCQUOTA_CTX_LIMIT", None)
        else:
            os.environ["CCQUOTA_CTX_LIMIT"] = str(limit)
        try:
            importlib.reload(q)                  # module reads the env at import
            return q.build({"transcript_path": self.transcript(ctx_tokens)})
        finally:
            os.environ.pop("CCQUOTA_SEGMENTS", None)
            os.environ.pop("CCQUOTA_CTX_LIMIT", None)
            importlib.reload(q)

    def test_reports_raw_tokens_without_a_limit(self):
        """No denominator exists on disk, so do not invent one."""
        self.assertIn("120k", self.render(120000))

    def test_reports_a_percentage_once_a_limit_is_given(self):
        self.assertIn("60%", self.render(120000, limit=200000))


class TestRender(TempConfig):
    def test_build_never_raises_on_empty_input(self):
        os.environ["CCQUOTA_SEGMENTS"] = "5h,wk,today,agents"
        try:
            self.assertIsInstance(q.build({}), str)
        finally:
            os.environ.pop("CCQUOTA_SEGMENTS", None)

    def test_unknown_segment_is_ignored(self):
        os.environ["CCQUOTA_SEGMENTS"] = "nonsense,today"
        try:
            self.assertIn("today", q.build({}))
        finally:
            os.environ.pop("CCQUOTA_SEGMENTS", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
