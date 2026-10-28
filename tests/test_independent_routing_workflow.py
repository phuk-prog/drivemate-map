"""The disabled legacy map builder must not be the only UK graph build entrypoint."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class IndependentRoutingWorkflowTest(unittest.TestCase):
    def workflow(self):
        path = ROOT / '.github/workflows/routing-uk.yml'
        self.assertTrue(path.is_file(), 'UK routing needs a workflow independent of disabled map-data.yml')
        return yaml.safe_load(path.read_text())

    def test_manual_validation_defaults_to_no_publication(self):
        workflow = self.workflow()
        triggers = workflow.get('on', workflow.get(True))
        self.assertEqual({'workflow_dispatch'}, set(triggers))
        self.assertIs(triggers['workflow_dispatch']['inputs']['validate_only']['default'], True)
        self.assertIs(workflow['concurrency']['cancel-in-progress'], False)
        steps = workflow['jobs']['routing']['steps']
        publish = next(s for s in steps if s.get('name') == 'Publish the road graph')
        self.assertIn("github.ref == 'refs/heads/main'", publish['if'])
        self.assertIn('inputs.validate_only == false', publish['if'])

    def test_routing_is_independent_and_preserves_existing_graph_gates(self):
        workflow = self.workflow()
        job = workflow['jobs']['routing']
        self.assertNotIn('needs', job)
        self.assertNotIn('if', job)
        legacy = yaml.safe_load((ROOT / '.github/workflows/map-data.yml').read_text())
        expected = legacy['jobs']['routing']['steps']
        self.assertEqual(expected, job['steps'])
        names = [s.get('name') for s in job['steps']]
        self.assertLess(names.index('Test journeys across the UK (must all work before publishing)'),
                        names.index('Publish the road graph'))
        self.assertLess(names.index('Survey extra UK road connections (diagnostics, never invented turns)'),
                        names.index('Publish the road graph'))
