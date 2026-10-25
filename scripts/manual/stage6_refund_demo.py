from app.graph import run_supportflow

result = run_supportflow(
    "I want a $5000 refund for order #1006.",
    authenticated_customer_id=1,
)

print("Classification:", result.get("classification"))
print("Approval status:", result.get("approval_status"))

print("\nTool calls:")
for call in result.get("tool_calls") or []:
    print(call)

print("\nFinal response:")
print(result.get("final_response"))

print("\nExecution log:")
for line in result.get("execution_log") or []:
    print(line)
