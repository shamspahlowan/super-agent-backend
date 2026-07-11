import sys
from pathlib import Path

ROOT_DIRECTORY = Path(__file__).resolve().parents[1]

if str(ROOT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(ROOT_DIRECTORY))

from app.core.config import get_settings
from app.explanations.service import (
    OpenAIExplanationGenerator,
)


def main() -> None:
    settings = get_settings()

    if settings.openai_api_key is None:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured."
        )

    generator = OpenAIExplanationGenerator(
        api_key=(
            settings.openai_api_key
            .get_secret_value()
        ),
        model=(
            settings.openai_explanation_model
        ),
        timeout_seconds=(
            settings
            .openai_explanation_timeout_seconds
        ),
        max_output_tokens=(
            settings
            .openai_explanation_max_output_tokens
        ),
    )

    print(
        "OpenAI explanation generator initialized."
    )

    print(
        f"Configured model: {generator.model}"
    )


if __name__ == "__main__":
    main()