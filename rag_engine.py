import glob
import json
import os
import re
from typing import Any

import chromadb
import requests


EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "sre_runbooks_minilm_v1"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
OLLAMA_MODEL = "llama3.2"


class RunbookRAG:
    """Retrieve the closest runbook and use it to create a grounded diagnosis."""

    def __init__(self, runbook_dir: str = "runbooks"):
        self.runbook_dir = runbook_dir
        self._embedding_model = None

        # Chroma stores vectors on disk, so the runbooks do not need to be
        # embedded again after the first successful startup.
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
        self.collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        # Add runbooks that are not in the collection yet; preserve existing
        # records and their IDs so local Chroma data never needs to be deleted.
        self._ingest_missing_runbooks()

    def _get_embedding_model(self):
        """Load the local sentence-embedding model only when it is first needed."""
        if self._embedding_model is None:
            from sentence_transformers import SentenceTransformer

            # Use Apple's Metal acceleration when PyTorch reports that it is
            # available; otherwise SentenceTransformers runs on the CPU.
            import torch

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            self._embedding_model = SentenceTransformer(
                EMBEDDING_MODEL,
                device=device,
            )
        return self._embedding_model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Turn text into numeric vectors for Chroma to store or compare."""
        vectors = self._get_embedding_model().encode(
            texts,
            normalize_embeddings=True,
        )
        return vectors.tolist()

    def _ingest_missing_runbooks(self) -> None:
        """Upsert runbooks whose source files are not already indexed."""
        paths = sorted(glob.glob(os.path.join(self.runbook_dir, "*.md")))
        if not paths:
            raise RuntimeError(
                f"No Markdown runbooks found in {self.runbook_dir!r}; "
                "at least one runbook is required."
            )

        existing_records = self.collection.get(include=["metadatas"])
        existing_ids = set(existing_records["ids"])
        existing_source_ids = {
            metadata["source"]: record_id
            for record_id, metadata in zip(
                existing_records["ids"],
                existing_records["metadatas"],
            )
            if metadata and metadata.get("source")
        }

        documents = []
        sources = []
        record_ids = []
        for path in paths:
            source = os.path.basename(path)
            # Older versions used numeric IDs. Reuse those IDs when their
            # source metadata matches instead of adding duplicate documents.
            record_id = existing_source_ids.get(source, f"runbook-{source}")
            if record_id in existing_ids:
                continue

            with open(path, "r", encoding="utf-8") as runbook_file:
                documents.append(runbook_file.read())
            sources.append(source)
            record_ids.append(record_id)

        if not documents:
            return

        # Embed only new runbooks; this keeps normal app startup fast.
        self.collection.upsert(
            ids=record_ids,
            documents=documents,
            embeddings=self._embed(documents),
            metadatas=[{"source": source} for source in sources],
        )

    def retrieve_runbook(self, error_log: str) -> dict[str, Any]:
        """Return the closest runbook, its text, and Chroma's vector distance."""
        if not error_log.strip():
            raise ValueError("error_log must not be empty.")

        result = self.collection.query(
            query_embeddings=self._embed([error_log]),
            n_results=1,
            include=["documents", "metadatas", "distances"],
        )

        # Chroma returns one list per query. Our request contains one query, so
        # the first element in each result list is the nearest matching runbook.
        documents = result["documents"][0]
        if not documents:
            raise RuntimeError("The ChromaDB runbook collection is empty.")

        metadata = result["metadatas"][0][0] or {}
        collection_metadata = self.collection.metadata or {}
        return {
            "source": metadata.get("source", "unknown"),
            "content": documents[0],
            "distance": float(result["distances"][0][0]),
            "distance_metric": collection_metadata.get("hnsw:space", "l2"),
        }

    def health_check(self) -> dict[str, Any]:
        """Report healthy only when Chroma can read a non-empty runbook collection."""
        runbook_count = self.collection.count()
        if runbook_count < 1:
            raise RuntimeError("The ChromaDB runbook collection is empty.")
        return {"status": "healthy", "runbooks_loaded": runbook_count}

    def generate_ai_diagnosis(
        self,
        error_log: str,
        runbook_content: str,
    ) -> dict[str, Any]:
        """Create a JSON diagnosis using Groq, Ollama, or a deterministic fallback.

        Groq is preferred when GROQ_API_KEY is set. Otherwise, OLLAMA_URL
        selects a local OpenAI-compatible Ollama endpoint. With neither set,
        the method extracts useful steps directly from the retrieved runbook.
        """
        groq_api_key = os.getenv("GROQ_API_KEY")
        ollama_url = os.getenv("OLLAMA_URL")

        if groq_api_key:
            backend = "groq"
            url = GROQ_CHAT_URL
            model = os.getenv("GROQ_MODEL", GROQ_MODEL)
            headers = {"Authorization": f"Bearer {groq_api_key}"}
        elif ollama_url:
            backend = "ollama"
            url = self._ollama_chat_url(ollama_url)
            model = os.getenv("OLLAMA_MODEL", OLLAMA_MODEL)
            headers = {}
        else:
            return {
                "backend": "offline",
                "diagnosis": self._offline_diagnosis(error_log, runbook_content),
            }

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a careful Site Reliability Engineer. Diagnose the "
                    "incident using only the supplied alert and runbook. Do not "
                    "invent facts or commands. If the runbook does not establish "
                    "a cause, say that the cause is uncertain. Reply with one "
                    "JSON object only, with exactly these fields: root_cause "
                    "(string), immediate_triage_commands (array of strings), "
                    "permanent_remediation (array of strings)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"ALERT LOG:\n{error_log}\n\n"
                    f"RETRIEVED RUNBOOK:\n{runbook_content}"
                ),
            },
        ]
        response = requests.post(
            url,
            headers=headers,
            json={
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": messages,
            },
            timeout=(5, 120),
        )
        response.raise_for_status()

        response_body = response.json()
        try:
            content = response_body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError(
                f"{backend} returned a response without a chat message."
            ) from error

        return {
            "backend": backend,
            "diagnosis": self._parse_diagnosis(content),
        }

    @staticmethod
    def _ollama_chat_url(base_url: str) -> str:
        """Accept an Ollama server URL, /v1 base URL, or full chat URL."""
        url = base_url.rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        if url.endswith("/v1"):
            return f"{url}/chat/completions"
        return f"{url}/v1/chat/completions"

    @staticmethod
    def _parse_diagnosis(content: str) -> dict[str, Any]:
        """Parse and validate the three fields required from an LLM response."""
        try:
            diagnosis = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError("The LLM response was not valid JSON.") from error

        if not isinstance(diagnosis, dict):
            raise ValueError("The LLM response must be a JSON object.")

        root_cause = diagnosis.get("root_cause")
        immediate_commands = diagnosis.get("immediate_triage_commands")
        permanent_remediation = diagnosis.get("permanent_remediation")
        if (
            not isinstance(root_cause, str)
            or not isinstance(immediate_commands, list)
            or not all(isinstance(item, str) for item in immediate_commands)
            or not isinstance(permanent_remediation, list)
            or not all(isinstance(item, str) for item in permanent_remediation)
        ):
            raise ValueError(
                "The LLM JSON must contain a string root_cause and arrays of "
                "strings for immediate_triage_commands and permanent_remediation."
            )

        return {
            "root_cause": root_cause,
            "immediate_triage_commands": immediate_commands,
            "permanent_remediation": permanent_remediation,
        }

    @classmethod
    def _offline_diagnosis(
        cls,
        error_log: str,
        runbook_content: str,
    ) -> dict[str, Any]:
        """Build a predictable diagnosis from runbook headings and list items."""
        sections: dict[str, list[str]] = {}
        current_section = ""
        for line in runbook_content.splitlines():
            heading = re.match(r"^##\s+(.+)$", line.strip())
            if heading:
                current_section = heading.group(1).strip().lower()
                sections[current_section] = []
            elif current_section and line.strip():
                sections[current_section].append(line.strip())

        symptom_lines = sections.get("symptom", [])
        root_cause = " ".join(symptom_lines).strip()
        if not root_cause:
            root_cause = (
                "The runbook does not state a specific cause. Investigate the "
                f"alert using the retrieved runbook. Alert: {error_log}"
            )

        return {
            "root_cause": root_cause,
            "immediate_triage_commands": cls._list_items(
                sections.get("diagnostic commands", [])
            )
            or ["Review the retrieved runbook for diagnostic steps."],
            "permanent_remediation": cls._list_items(
                sections.get("remediation", [])
            )
            or ["Follow the retrieved runbook's remediation guidance."],
        }

    @staticmethod
    def _list_items(lines: list[str]) -> list[str]:
        """Keep numbered or bulleted runbook steps and remove their list markers."""
        items = []
        for line in lines:
            item = re.sub(r"^(?:\d+[.)]\s*|[-*]\s*)", "", line).strip()
            if item:
                items.append(item)
        return items
