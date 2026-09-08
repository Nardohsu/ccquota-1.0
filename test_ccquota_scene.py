import os
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import ccquota_scene as s


class TestSceneClock(unittest.TestCase):
    def test_boundaries_and_midnight(self):
        for clock, boundary in [('2026-09-08 08:59:59', '2026-09-07 18:00:00'),
                                ('2026-09-08 09:00:00', '2026-09-08 09:00:00'),
                                ('2026-09-08 17:59:59', '2026-09-08 09:00:00'),
                                ('2026-09-08 18:00:00', '2026-09-08 18:00:00'),
                                ('2026-09-09 00:00:00', '2026-09-08 18:00:00')]:
            with self.subTest(clock=clock):
                self.assertEqual(s.scene_slot(datetime.fromisoformat(clock)), datetime.fromisoformat(boundary))

    def test_repeat_clock_rollback_and_wake(self):
        player = SimpleNamespace(closed=False, last_slot=datetime(2026, 9, 7, 18),
                                 switch=Mock(), master=Mock(), check_clock=Mock())
        for now in [datetime(2026, 9, 8, 9), datetime(2026, 9, 8, 9, 0, 1),
                    datetime(2026, 9, 8, 8), datetime(2026, 9, 8, 9)]:
            s.ScenePlayer.check_clock(player, now)
        self.assertEqual(player.switch.call_count, 1)
        s.ScenePlayer.check_clock(player, datetime(2026, 9, 8, 18))
        self.assertEqual(player.switch.call_count, 2)
        s.ScenePlayer.check_clock(player, datetime(2026, 9, 11, 12))
        self.assertEqual(player.switch.call_count, 3)

    def test_directory_filter_and_missing_directory(self):
        with TemporaryDirectory() as folder:
            for name in ['a.PNG', 'b.png', 'README.md', 'ignore.jpg']:
                Path(folder, name).touch()
            Path(folder, 'folder.png').mkdir()
            self.assertEqual({Path(p).name for p in s.scene_files(folder)}, {'a.PNG', 'b.png'})
            self.assertEqual(s.scene_files(os.path.join(folder, 'missing')), [])


@unittest.skipUnless(os.environ.get('CCQUOTA_TEST_GUI') == '1', 'opt-in native desktop checks')
class TestSceneAnimation(unittest.TestCase):
    def test_transition_resize_refresh_and_cleanup(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with TemporaryDirectory() as folder:
                for name, color in [('a.png', '#ff0000'), ('b.png', '#00ff00')]:
                    photo = tk.PhotoImage(master=root, width=4, height=4)
                    photo.put(color, to=(0, 0, 4, 4))
                    photo.write(os.path.join(folder, name), format='png')
                Path(folder, 'broken.png').write_text('invalid image')
                canvas = tk.Canvas(root)
                size = [80, 40]
                def redraw():
                    canvas.delete('all')
                    player.render(canvas, 0, 0, *size)
                player = s.ScenePlayer(root, folder, redraw)
                redraw()
                first = player.current_path
                self.assertIsNotNone(first)
                with patch.object(s.time, 'monotonic', return_value=100):
                    self.assertTrue(player.switch())
                    self.assertFalse(player.switch())  # Repeated clicks cannot stack transitions.
                def frame(elapsed):
                    root.after_cancel(player.frame_id)
                    with patch.object(s.time, 'monotonic', return_value=100+elapsed):
                        player.animate()
                frame(.5)
                self.assertTrue(0 < player.alpha < 255)
                self.assertEqual(canvas.itemcget('scene-shade', 'state'), 'normal')
                self.assertEqual(player.current_path, first)
                # A full panel refresh/resize during animation preserves the shade.
                size[:] = [120, 50]
                redraw()
                self.assertEqual(player.shade.width(), 118)
                frame(1)
                self.assertEqual(player.alpha, 255)
                self.assertNotEqual(player.current_path, first)
                frame(1.5)
                self.assertTrue(0 < player.alpha < 255)
                frame(2)
                self.assertEqual(player.alpha, 0)
                self.assertFalse(player.transitioning)
                self.assertEqual(canvas.itemcget('scene-shade', 'state'), 'hidden')
                # The current scene can disappear on disk; keep it until a valid replacement exists.
                for path in Path(folder).iterdir():
                    path.unlink()
                self.assertFalse(player.switch())
                self.assertIsNotNone(player.art)
                player.close()
                self.assertIsNone(player.clock_id)
                self.assertIsNone(player.frame_id)
        finally:
            root.destroy()

    def test_empty_single_and_new_image(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with TemporaryDirectory() as folder:
                player = s.ScenePlayer(root, folder, Mock())
                self.assertIsNone(player.art)
                self.assertFalse(player.switch())
                photo = tk.PhotoImage(master=root, width=2, height=2)
                photo.write(os.path.join(folder, 'added.png'), format='png')
                self.assertTrue(player.switch())
                self.assertIsNotNone(player.art)
                self.assertFalse(player.switch())
                player.close()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
