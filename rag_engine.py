
import os
import glob

class RunbookRAG:
    """
    Lightweight Retrieval-Augmented Generation (RAG) retriever.
    Loads operational Markdown runbooks and matches incoming incident logs
    to the relevant standard operating procedure (SOP) before LLM triage.
    """
    def __init__(self, runbook_dir: str = "runbooks"):
        self.runbooks = {}
        for filepath in glob.glob(os.path.join(runbook_dir, "*.md")):
            with open(filepath, "r", encoding="utf-8") as f:
                self.runbooks[os.path.basename(filepath)] = f.read()

    def retrieve_runbook(self, error_log: str) -> dict:
        log_lower = error_log.lower()
        best_match = None
        best_score = 0

        # Keyword & semantic token scoring across runbooks
        keywords = {
            "oom_killed.md": ["137", "oomkilled", "out of memory", "memory", "sigkill"],
            "bad_gateway_502.md": ["502", "bad gateway", "connection refused", "endpoints", "selector"],
            "cpu_throttling.md": ["504", "gateway timeout", "cfs", "throttl", "latency", "p99"]
        }

        for filename, terms in keywords.items():
            score = sum(1 for term in terms if term in log_lower)
            if score > best_score and filename in self.runbooks:
                best_score = score
                best_match = filename

        if best_match:
            return {"source": best_match, "content": self.runbooks[best_match]}
        return {"source": "general_sre_fallback", "content": "Follow standard 3-phase SRE triage: Stop bleeding, isolate boundary, add guardrail."}

if __name__ == "__main__":
    # Quick local test
    rag = RunbookRAG()
    sample_alert = "ALERT: Pod payment-service-7f8b9 crashed with OOMKilled and Exit Code 137"
    result = rag.retrieve_runbook(sample_alert)
    print(f"Matched Runbook: {result['source']}\n")
    print(result["content"])
