from app.graph import run_supportflow


def show_result(name, result):
    print("\n" + "=" * 70)
    print(name)
    print("=" * 70)

    print("\nClassification:")
    print(result.get("classification"))

    print("\nRetrieved documents:")
    for doc in result.get("retrieved_documents") or []:
        print(
            f"  - {doc.get('title')} | "
            f"similarity={doc.get('cosine_similarity')}"
        )

    print("\nTool calls:")
    for call in result.get("tool_calls") or []:
        print(
            f"  - {call.get('tool_name')} "
            f"{call.get('tool_args')} | "
            f"validation={call.get('validation_passed')} | "
            f"authorization={call.get('authorization_passed')} | "
            f"executed={call.get('backend_executed')}"
        )

    print("\nApproval status:")
    print(result.get("approval_status"))

    print("\nFinal response:")
    print(result.get("final_response"))

    print("\nExecution log:")
    for line in result.get("execution_log") or []:
        print(line)


# TEST 1
result = run_supportflow(
    "What is the status of order #1003?",
    authenticated_customer_id=1,
)
show_result("TEST 1 — ORDER LOOKUP", result)


# TEST 2
result = run_supportflow(
    "My order #1003 arrived damaged. Can you get me a replacement?",
    authenticated_customer_id=1,
)
show_result("TEST 2 — MULTI-STEP REPLACEMENT", result)


# TEST 3
result = run_supportflow(
    "What is the status of order #1002?",
    authenticated_customer_id=1,
)
show_result("TEST 3 — UNAUTHORIZED ORDER", result)


# TEST 4
result = run_supportflow(
    "How long does express shipping take?",
    authenticated_customer_id=1,
)
show_result("TEST 4 — RAG SHIPPING", result)


# TEST 5
result = run_supportflow(
    "I want a $5000 refund for order #1006.",
    authenticated_customer_id=1,
)
show_result("TEST 5 — HIGH VALUE REFUND", result)


# TEST 6
result = run_supportflow(
    "What is the capital of France?",
    authenticated_customer_id=1,
)
show_result("TEST 6 — OUT OF DOMAIN", result)
