import unittest
from pathlib import Path
from unittest.mock import patch

import chromadb
from fastapi.testclient import TestClient

from rag_engine import RunbookRAG


ROOT = Path(__file__).parent
RUNBOOK_DIR = ROOT / "runbooks"
RUNBOOK_NAMES = {
    "oom_killed.md",
    "bad_gateway_502.md",
    "cpu_throttling.md",
    "node_disk_pressure.md",
    "kafka_consumer_lag.md",
}


class FakeCollection:
    def __init__(self, records=None):
        self.metadata = {"hnsw:space": "cosine"}
        self.records = records or {}
        self.upsert_calls = []

    def count(self):
        return len(self.records)

    def get(self, include=None):
        return {
            "ids": list(self.records),
            "metadatas": [
                record["metadata"] for record in self.records.values()
            ],
        }

    def upsert(self, ids, documents, embeddings, metadatas):
        self.upsert_calls.append(list(ids))
        for record_id, document, embedding, metadata in zip(
            ids, documents, embeddings, metadatas
        ):
            self.records[record_id] = {
                "document": document,
                "embedding": embedding,
                "metadata": metadata,
            }


class FakeChromaClient:
    def __init__(self, collection):
        self.collection = collection

    def get_or_create_collection(self, name, metadata):
        return self.collection


def make_legacy_records():
    legacy_sources = [
        "bad_gateway_502.md",
        "cpu_throttling.md",
        "oom_killed.md",
    ]
    return {
        f"runbook-{index}": {
            "document": "",
            "embedding": [],
            "metadata": {"source": source},
        }
        for index, source in enumerate(legacy_sources)
    }


# Importing app normally opens the developer's persistent Chroma database.
# Use a temporary in-memory stand-in so importing this test never alters it.
_app_import_collection = FakeCollection(
    {
        f"runbook-{name}": {
            "document": "",
            "embedding": [],
            "metadata": {"source": name},
        }
        for name in RUNBOOK_NAMES
    }
)
with patch.object(
    chromadb,
    "PersistentClient",
    return_value=FakeChromaClient(_app_import_collection),
):
    import app as app_module


class ScenarioRAG:
    """Provide deterministic retrieved runbooks for API route tests."""

    scenarios = {
        "oomkilled": "oom_killed.md",
        "502": "bad_gateway_502.md",
        "cfs": "cpu_throttling.md",
        "diskpressure": "node_disk_pressure.md",
        "commitfailedexception": "kafka_consumer_lag.md",
    }

    def health_check(self):
        return {"status": "healthy", "runbooks_loaded": len(RUNBOOK_NAMES)}

    def retrieve_runbook(self, error_log):
        lowered_log = error_log.lower()
        source = next(
            name for marker, name in self.scenarios.items() if marker in lowered_log
        )
        content = (RUNBOOK_DIR / source).read_text(encoding="utf-8")
        return {
            "source": source,
            "content": content,
            "distance": 0.2,
            "distance_metric": "cosine",
        }

    def generate_ai_diagnosis(self, error_log, runbook_content):
        return {
            "backend": "offline",
            "diagnosis": RunbookRAG._offline_diagnosis(
                error_log,
                runbook_content,
            ),
        }


class AgentAPITests(unittest.TestCase):
    def setUp(self):
        app_module.rag = ScenarioRAG()
        self.client = TestClient(app_module.app)

    def test_health_checks_runbook_store(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runbooks_loaded"], 5)

    def test_metrics_exposes_triage_counters_and_stage_histograms(self):
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        for metric in (
            "sre_agent_triage_requests_total",
            "sre_agent_rag_retrieval_seconds",
            "sre_agent_llm_inference_seconds",
        ):
            self.assertIn(metric, response.text)

    def test_triage_returns_all_five_runbook_scenarios(self):
        scenarios = {
            "OOMKilled with exit code 137": "oom_killed.md",
            "HTTP 502 Bad Gateway upstream connection refused": "bad_gateway_502.md",
            "HTTP 504 high p99 CFS throttling": "cpu_throttling.md",
            "node DiskPressure root filesystem nearly full": "node_disk_pressure.md",
            "Kafka consumer lag CommitFailedException rebalance": "kafka_consumer_lag.md",
        }
        for error_log, expected_runbook in scenarios.items():
            with self.subTest(runbook=expected_runbook):
                response = self.client.post(
                    "/triage",
                    json={"error_log": error_log},
                )
                self.assertEqual(response.status_code, 200)
                result = response.json()
                self.assertEqual(result["matched_runbook"], expected_runbook)
                self.assertEqual(result["llm_backend"], "offline")
                self.assertTrue(result["diagnosis"]["root_cause"])
                self.assertTrue(result["diagnosis"]["immediate_triage_commands"])
                self.assertTrue(result["diagnosis"]["permanent_remediation"])


class RunbookIndexingTests(unittest.TestCase):
    def test_startup_upserts_only_runbooks_missing_from_existing_collection(self):
        collection = FakeCollection(make_legacy_records())
        fake_client = FakeChromaClient(collection)
        with (
            patch.object(
                chromadb,
                "PersistentClient",
                return_value=fake_client,
            ),
            patch.object(
                RunbookRAG,
                "_embed",
                autospec=True,
                side_effect=lambda self, texts: [[0.1] * 4 for _ in texts],
            ) as embed,
        ):
            RunbookRAG(runbook_dir=str(RUNBOOK_DIR))

            expected_sources = {
                path.name for path in RUNBOOK_DIR.glob("*.md")
            }
            indexed_sources = {
                record["metadata"]["source"]
                for record in collection.records.values()
            }
            self.assertEqual(indexed_sources, expected_sources)
            self.assertEqual(collection.count(), 5)
            self.assertEqual(len(collection.upsert_calls), 1)
            self.assertEqual(len(collection.upsert_calls[0]), 2)
            self.assertEqual(embed.call_count, 1)

            # A second startup sees those IDs and does not add duplicates.
            RunbookRAG(runbook_dir=str(RUNBOOK_DIR))
            self.assertEqual(collection.count(), 5)
            self.assertEqual(len(collection.upsert_calls), 1)


if __name__ == "__main__":
    unittest.main()
