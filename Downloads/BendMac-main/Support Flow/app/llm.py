import json
import os
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from groq import Groq

load_dotenv()


def classify_ticket(message: str) -> dict:
    """Classifies a customer support ticket into category, priority, and sentiment using Groq.

    Args:
        message (str): The customer support ticket message text.

    Returns:
        dict: Dictionary with keys 'category', 'priority', and 'sentiment'.

    Raises:
        ValueError: If GROQ_API_KEY is missing, LLM returns empty response,
            invalid JSON, or missing required fields.
        RuntimeError: If the Groq API call fails.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set")

    client = Groq(api_key=api_key)

    system_prompt = (
        "You are an AI customer support ticket classifier. "
        "Analyze the customer message and classify it into:\n"
        "- category (e.g., Technical Support, Billing, Order Issue, General Inquiry, Refund Request)\n"
        "- priority (e.g., LOW, MEDIUM, HIGH, URGENT)\n"
        "- sentiment (e.g., POSITIVE, NEUTRAL, NEGATIVE, FRUSTRATED)\n\n"
        "You MUST respond ONLY with a valid JSON object with no markdown formatting, preambles, or explanations. "
        "The JSON object MUST contain exactly these three keys: \"category\", \"priority\", and \"sentiment\"."
    )

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
    except Exception as err:
        raise RuntimeError(f"Groq API request failed: {err}") from err

    content = response.choices[0].message.content
    if not content:
        raise ValueError("Groq API returned an empty response content")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as err:
        raise ValueError(f"Failed to parse invalid JSON from Groq LLM: {err}. Raw content: {content!r}") from err

    required_fields = {"category", "priority", "sentiment"}
    missing_fields = required_fields - set(data.keys())
    if missing_fields:
        raise ValueError(f"LLM JSON response is missing required keys: {missing_fields}. Data: {data}")

    return data
