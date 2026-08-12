import json
import tempfile
import unittest
from pathlib import Path

from csv_power_tool.watch import (
    WATCH_STATE_FORMAT,
    WATCH_STATE_VERSION,
    WatchCoordinator,
    WatchStateError,
    workflow_fingerprint,
)


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class WatchCoordinatorTests(unittest.TestCase):
    def test_settles_changes_and_suppresses_duplicate_runs_after_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            state_path = root / "watch.json"
            input_path.write_text("id\n1\n", encoding="utf-8")
            clock = _Clock()
            coordinator = WatchCoordinator(state_path, "workflow-a", settle_seconds=2, clock=clock)

            self.assertFalse(coordinator.observe([input_path]).should_process)
            clock.advance(1)
            self.assertEqual(coordinator.observe([input_path]).action, "settling")
            clock.advance(1)
            ready = coordinator.observe([input_path])
            self.assertTrue(ready.should_process)
            coordinator.mark_result(ready.run_id, 0)

            restarted_clock = _Clock()
            restarted = WatchCoordinator(state_path, "workflow-a", settle_seconds=2, clock=restarted_clock)
            self.assertFalse(restarted.observe([input_path]).should_process)
            restarted_clock.advance(2)
            self.assertEqual(restarted.observe([input_path]).action, "unchanged")
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["last_event"], "unchanged")

    def test_replacement_truncation_and_failure_are_observable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "rotating.csv"
            state_path = root / "watch.json"
            input_path.write_text("id,value\n1,old\n", encoding="utf-8")
            clock = _Clock()
            coordinator = WatchCoordinator(state_path, "workflow-a", settle_seconds=1, clock=clock)

            coordinator.observe([input_path])
            clock.advance(1)
            first = coordinator.observe([input_path])
            coordinator.mark_result(first.run_id, 3)
            self.assertEqual(coordinator.state["last_event"], "failed")
            self.assertFalse(coordinator.observe([input_path]).should_process)

            input_path.write_text("id,value\n1,new\n", encoding="utf-8")
            self.assertEqual(coordinator.observe([input_path]).action, "changed")
            clock.advance(1)
            replacement = coordinator.observe([input_path])
            self.assertTrue(replacement.should_process)
            coordinator.mark_result(replacement.run_id, 0)

            input_path.write_text("id\n", encoding="utf-8")
            self.assertEqual(coordinator.observe([input_path]).action, "changed")
            clock.advance(1)
            truncation = coordinator.observe([input_path])
            self.assertTrue(truncation.should_process)
            coordinator.mark_result(truncation.run_id, 0)

    def test_deletion_and_reappearance_do_not_process_empty_inputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            state_path = root / "watch.json"
            input_path.write_text("id\n1\n", encoding="utf-8")
            clock = _Clock()
            coordinator = WatchCoordinator(state_path, "workflow-a", settle_seconds=1, clock=clock)
            coordinator.observe([input_path])
            clock.advance(1)
            ready = coordinator.observe([input_path])
            coordinator.mark_result(ready.run_id, 0)

            input_path.unlink()
            deleted = coordinator.observe([])
            self.assertEqual(deleted.action, "deleted")
            clock.advance(1)
            deleted = coordinator.observe([])
            self.assertFalse(deleted.should_process)
            self.assertIn(str(input_path.resolve()), coordinator.state["last_deleted_paths"])

            input_path.write_text("id\n2\n", encoding="utf-8")
            self.assertEqual(coordinator.observe([input_path]).action, "changed")
            clock.advance(1)
            self.assertTrue(coordinator.observe([input_path]).should_process)

    def test_future_or_corrupt_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "watch.json"
            state_path.write_text(
                json.dumps({"format": WATCH_STATE_FORMAT, "version": WATCH_STATE_VERSION + 1}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WatchStateError, "newer than supported"):
                WatchCoordinator(state_path, "workflow-a")

            state_path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(WatchStateError):
                WatchCoordinator(state_path, "workflow-a")

    def test_workflow_fingerprint_changes_with_config_or_pattern(self):
        first = workflow_fingerprint({"dedupe": False}, ["in/*.csv"], "out.csv")
        self.assertNotEqual(first, workflow_fingerprint({"dedupe": True}, ["in/*.csv"], "out.csv"))
        self.assertNotEqual(first, workflow_fingerprint({"dedupe": False}, ["in/*.tsv"], "out.csv"))


if __name__ == "__main__":
    unittest.main()
