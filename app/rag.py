"""RAG answer generation — retrieval-augmented response using knowledge base + Groq.

Provides `answer_with_knowledge_base(query)` which:
  1. Retrieves relevant policy documents via search_knowledge_base()
  2. Constructs a context block from retrieved content
  3. Sends query + context to Groq for grounded answer generation
  4. Returns structured result with retrieval metadata + LLM answer

This module is intentionally isolated from the ticket-processing and
tool-calling pipelines.  It will be integrated into the main workflow
in a later stage.
"""

import json
import time
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from app.llm_client import chat_completion

from app.kb import search_knowledge_base
from app.observability import traced_span, record_llm_call, hash_sensitive, safe_set_attribute

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────
RAG_TOP_K = 3
# Minimum cosine similarity to consider a section relevant. 0.30 refused 95%
# of out-of-scope questions while answering 99% of in-scope ones in
# evaluation/retrieval_eval.py; the old 0.15 refused almost none.
RAG_SIMILARITY_THRESHOLD = 0.30


def _build_context_block(documents: list[dict]) -> str:
    """Formats retrieved knowledge documents into a numbered context block.

    Args:
        documents: List of document dicts from search_knowledge_base().

    Returns:
        A formatted string with numbered policy sections.
    """
    sections = []
    for i, doc in enumerate(documents, 1):
        heading = f" — {doc['heading']}" if doc.get("heading") else ""
        sections.append(
            f"[Policy {i}] {doc['title']}{heading}\n"
            f"Source: {doc['source']}\n"
            f"{doc['content']}"
        )
    return "\n\n".join(sections)


def answer_with_knowledge_base(
    query: str,
    top_k: int = RAG_TOP_K,
    similarity_threshold: float = RAG_SIMILARITY_THRESHOLD,
) -> dict:
    """Generates a grounded answer to a query using retrieved knowledge base context.

    Pipeline:
        query → search_knowledge_base() → context block → Groq LLM → answer

    Args:
        query:                Natural-language question.
        top_k:                Maximum documents to retrieve.
        similarity_threshold: Minimum cosine similarity to consider a document relevant.

    Returns:
        dict with keys:
            query               – original question
            relevant_context    – whether any relevant policy was found (bool)
            retrieved_documents – list of {title, heading, content, source, cosine_similarity, rerank_score}
            answer              – LLM-generated answer or "no relevant policy" message
            model               – LLM model used (or None if LLM was not called)

    Raises:
        ValueError: If query is empty.
        RuntimeError: If Groq API call fails.
    """
    with traced_span("rag.answer") as span:
        if not query or not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string")

        if span:
            safe_set_attribute(span, "query_hash", hash_sensitive(query))
            safe_set_attribute(span, "top_k", top_k)
            safe_set_attribute(span, "similarity_threshold", similarity_threshold)

        # ── Step 1: Retrieve relevant documents ───────────────────────
        with traced_span("rag.retrieval") as retrieval_span:
            retrieved = search_knowledge_base(
                query=query,
                top_k=top_k,
                similarity_threshold=similarity_threshold,
            )

            if retrieval_span:
                safe_set_attribute(retrieval_span, "documents_retrieved", len(retrieved))
                if retrieved:
                    doc_ids = [doc.get("title", "unknown") for doc in retrieved]
                    scores = [doc.get("cosine_similarity", 0.0) for doc in retrieved]
                    safe_set_attribute(retrieval_span, "document_ids", str(doc_ids))
                    safe_set_attribute(retrieval_span, "similarity_scores", str(scores))

        # Build retrieval metadata for the response
        doc_metadata = [
            {
                "title": doc["title"],
                "heading": doc.get("heading"),
                "content": doc["content"],
                "source": doc["source"],
                "cosine_similarity": doc["cosine_similarity"],
                "rerank_score": doc.get("rerank_score"),
            }
            for doc in retrieved
        ]

        # ── Step 2: Handle no relevant context ────────────────────────
        if not retrieved:
            if span:
                safe_set_attribute(span, "relevant_context", False)
            return {
                "query": query,
                "relevant_context": False,
                "retrieved_documents": doc_metadata,
                "answer": (
                    "I could not find any relevant company policy to answer your question. "
                    "Please contact a human support agent for assistance."
                ),
                "model": None,
            }

        # ── Step 3: Build context block from retrieved documents ──────
        context_block = _build_context_block(retrieved)

        # ── Step 4: Construct Groq prompt ─────────────────────────────
        system_prompt = (
            "You are a helpful customer support assistant for SupportFlow. "
            "You answer questions using ONLY the company policy context provided below.\n\n"
            "RULES:\n"
            "- Answer based EXCLUSIVELY on the provided policy context.\n"
            "- Do NOT invent, assume, or fabricate any policy information.\n"
            "- If the provided context does not contain enough information to fully "
            "answer the question, say so explicitly.\n"
            "- Be concise, helpful, and professional.\n"
            "- Reference the specific policy when possible.\n\n"
            "COMPANY POLICY CONTEXT:\n"
            "─────────────────────────────────────────\n"
            f"{context_block}\n"
            "─────────────────────────────────────────"
        )

        user_message = f"Customer question: {query}"

        # ── Step 5: Call Groq ─────────────────────────────────────────
        with traced_span("llm.groq.rag_answer") as llm_span:
            start = time.time()
            try:
                result = chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.0,
                )
            except ValueError:
                raise
            except Exception as err:
                raise RuntimeError(f"LLM RAG API request failed: {err}") from err
            response = result.response

            latency_ms = (time.time() - start) * 1000

            if llm_span:
                record_llm_call(llm_span, model=result.model, prompt_version="rag-v1", latency_ms=latency_ms)

        answer_content = response.choices[0].message.content
        if not answer_content:
            raise ValueError("Groq API returned an empty response for RAG query")

        if span:
            safe_set_attribute(span, "relevant_context", True)

        return {
            "query": query,
            "relevant_context": True,
            "retrieved_documents": doc_metadata,
            "answer": answer_content.strip(),
            "model": result.model,
        }
