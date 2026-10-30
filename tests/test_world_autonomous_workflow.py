"""GitHub itself orchestrates the world overview; no chat/manual rebuild loop."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def events(name):
    data = yaml.safe_load((ROOT / name).read_text())
    return data.get("on", data.get(True)), data


class AutonomousWorldMapTests(unittest.TestCase):
    def test_world_tiles_follow_only_successful_twice_daily_source_updates(self):
        source_events, source = events("world-overview-sources.yml")
        map_events, workflow = events("world-map-publish.yml")
        self.assertEqual({"0 0 * * *", "0 1 * * *", "0 13 * * *", "0 14 * * *"},
                         {x["cron"] for x in source_events["schedule"]})
        self.assertNotIn("push", source_events)
        self.assertNotIn("schedule", map_events, "Map builder must not run a duplicate timer")
        self.assertNotIn("push", map_events)
        self.assertEqual([source["name"]], map_events["workflow_run"]["workflows"])
        self.assertEqual(["completed"], map_events["workflow_run"]["types"])
        self.assertEqual(["main"], map_events["workflow_run"]["branches"])
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])
        self.assertIn("conclusion == 'success'", workflow["jobs"]["world-overview"]["if"])
        self.assertIn("event == 'schedule'", workflow["jobs"]["world-overview"]["if"])
        self.assertIn("head_branch == 'main'", workflow["jobs"]["world-overview"]["if"])
        self.assertFalse(map_events["workflow_dispatch"]["inputs"]["publish"]["default"])

    def test_world_checks_changed_identity_before_build_and_publishes_only_after_validation(self):
        _, wf = events("world-map-publish.yml")
        jobs = wf["jobs"]["world-overview"]["steps"]
        plan = next(i for i, s in enumerate(jobs) if s.get("id") == "plan")
        build = next(i for i, s in enumerate(jobs)
                     if s.get("name", "").startswith("Build and verify"))
        publish = next(i for i, s in enumerate(jobs)
                       if s.get("name", "").startswith("Promote only"))
        self.assertLess(plan, build)
        self.assertLess(build, publish)
        self.assertIn("steps.plan.outputs.due == 'true'", jobs[build]["if"])
        self.assertIn("steps.plan.outputs.due == 'true'", jobs[publish]["if"])
        self.assertIn("github.ref == 'refs/heads/main'", jobs[publish]["if"])
        self.assertIn("inputs.publish == true", jobs[publish]["if"])
        self.assertIn("scripts/world_tiles.py --verify", jobs[build]["run"])
        self.assertIn("sha256sum --check --strict",
                      next(s for s in jobs if s.get("name", "").startswith("Fetch pinned"))["run"])
        self.assertEqual("write", wf["permissions"]["contents"])
        self.assertFalse(any("map-data-ukmap" in str(step) for step in jobs))

    def test_candidate_build_remains_independent_of_automated_publication(self):
        events_candidate, workflow = events("world-overview-candidate.yml")
        self.assertNotIn("schedule", events_candidate)
        self.assertNotIn("workflow_run", events_candidate)
        self.assertEqual("read", workflow["permissions"]["contents"])
        jobs = workflow["jobs"]["candidate"]["steps"]
        self.assertFalse(any("world_map_publish.py" in str(step) for step in jobs))


if __name__ == "__main__":
    unittest.main()
