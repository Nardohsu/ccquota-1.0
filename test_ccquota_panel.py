"""Guild data semantics and opt-in native UI checks.

Run all native checks with CCQUOTA_TEST_GUI=1 python -m unittest.
"""
import time
import os
import unittest
from unittest.mock import patch

import ccquota_panel as p


class TestGuildData(unittest.TestCase):
    def test_gather_includes_cached_codex(self):
        codex = {"source": "app-server", "stale": False,
                 "five_hour": {"percent": 19, "resets_at": 2000},
                 "weekly": None, "reset_credits": 3, "at": 1000}
        with patch.object(p.q, 'collect', return_value={}), \
             patch.object(p.q, 'read_json', return_value={}), \
             patch.object(p.q, 'official_limits', return_value={}), \
             patch.object(p.q, 'live_sessions', return_value=[]), \
             patch.object(p.q, 'codex_limits', return_value=codex) as limits:
            self.assertEqual(p.gather()['codex'], codex)
            limits.assert_called_once()

    def test_remaining_is_not_used_percentage(self):
        self.assertEqual(p.remaining_percent({'pct': 33}), 67)
        self.assertEqual(p.remaining_percent({'pct': 100}), 0)
        self.assertEqual(p.remaining_percent({'pct': 0}), 100)
        self.assertIsNone(p.remaining_percent({'pct': None}))
        self.assertIsNone(p.remaining_percent({'tokens': 3200}))

    def test_remaining_is_bounded(self):
        self.assertEqual(p.remaining_percent({'pct': 110}), 0)
        self.assertEqual(p.remaining_percent({'pct': -3}), 100)

    def test_guild_assets_exclude_old_entries_and_cache_reads(self):
        now = time.time()
        entries = {'recent': [int((now-3600)//60), 42, 9000],
                   'week': [int((now-3*86400)//60), 58, 5000],
                   'old': [int((now-8*86400)//60), 2000, 0]}
        with patch.object(p.q, 'collect', return_value=entries), \
             patch.object(p.q, 'read_json', return_value={}), \
             patch.object(p.q, 'official_limits', return_value={}), \
             patch.object(p.q, 'live_sessions', return_value=[]), \
             patch.object(p.q, 'codex_limits', return_value=None):
            data = p.gather()
        self.assertEqual(data['guild_tokens'], 100)
        self.assertIsNone(data['seven']['pct'])


@unittest.skipUnless(os.environ.get('CCQUOTA_TEST_GUI') == '1', 'opt-in native desktop checks')
class TestGuildWindow(unittest.TestCase):
    def pump(self, app):
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            app.update()
            if not app.busy:
                return
            time.sleep(.02)
        self.fail('background refresh did not complete')

    def test_empty_data_failure_retry_and_details(self):
        with patch.object(p.q, 'collect', return_value={}), \
             patch.object(p.q, 'read_json', return_value={}), \
             patch.object(p.q, 'official_limits', return_value={}), \
             patch.object(p.q, 'live_sessions', return_value=[]), \
             patch.object(p.q, 'codex_limits', return_value=None):
            app = p.Panel()
            try:
                self.pump(app)
                self.assertIsNone(app.data['seven']['pct'])
                self.assertEqual(app.data['guild_tokens'], 0)
                app.canvas.event_generate('<Button-1>', x=180, y=220)
                app.update()
                self.assertTrue(app.details.winfo_exists())
                app.details.destroy()
                with patch.object(p, 'gather', side_effect=OSError('test')):
                    app.request_rebuild()
                    self.pump(app)
                    self.assertEqual(app.error, 'OSError')
                    self.assertEqual(app.refresh_button['text'], '重試')
                app.refresh_button.invoke()
                self.pump(app)
                self.assertIsNone(app.error)
                self.assertEqual(app.refresh_button['state'], 'normal')
            finally:
                app.destroy()


if __name__ == '__main__':
    unittest.main()
