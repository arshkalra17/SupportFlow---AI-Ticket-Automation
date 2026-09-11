"""Embedding generation layer — local Sentence Transformer embedding service.

Provides a modular interface `generate_embedding(text)` returning a 384-dimensional
dense semantic vector produced locally by `all-MiniLM-L6-v2`.
"""
# pyrefly: ignore [missing-import]
from sentence_transformers import SentenceTransformer

EMBEDDING_DIMENSION = 384
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# Cached singleton model instance
_model_instance: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    """Loads and caches the local SentenceTransformer model instance.

    Returns:
        SentenceTransformer: Loaded model instance.

    Raises:
        RuntimeError: If model initialization fails.
    """
    global _model_instance
    if _model_instance is None:
        try:
            _model_instance = SentenceTransformer(EMBEDDING_MODEL_NAME)
        except Exception as err:
            raise RuntimeError(
                f"Failed to load local SentenceTransformer model '{EMBEDDING_MODEL_NAME}': {err}"
            ) from err
    return _model_instance


def generate_embedding(text: str) -> list[float]:
    """Generates a 384-dimensional semantic embedding for the input text.

    Uses the local `all-MiniLM-L6-v2` Sentence Transformer model.
    Does not depend on external APIs or API keys.

    Args:
        text (str): Input text string to embed.

    Returns:
        list[float]: A vector of exactly 384 float values.

    Raises:
        ValueError: If text is empty or invalid.
        RuntimeError: If model loading or vector encoding fails.
    """
    if not text or not isinstance(text, str) or not text.strip():
        raise ValueError("Input text for generate_embedding must be a non-empty string")

    model = get_embedding_model()

    try:
        raw_vector = model.encode(text, convert_to_numpy=True)
        vector = raw_vector.tolist()
    except Exception as err:
        raise RuntimeError(f"SentenceTransformer embedding generation failed: {err}") from err

    if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"Generated embedding vector length {len(vector) if isinstance(vector, list) else type(vector)} "
            f"does not match expected dimension {EMBEDDING_DIMENSION}"
        )

    return [float(val) for val in vector]
