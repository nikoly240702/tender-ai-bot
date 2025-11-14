"""
Level 2 utilities: OpenAI client and prompts
"""

from .openai_client import OpenAIClient, create_client_from_env
from .prompts import (
    EXTRACT_NMCK_PROMPT,
    EXTRACT_DEADLINE_PROMPT,
    EXTRACT_GUARANTEE_PROMPT,
    EXTRACT_REQUIREMENTS_PROMPT,
    VERIFICATION_PROMPT,
    format_extraction_prompt,
    format_verification_prompt
)

__all__ = [
    'OpenAIClient',
    'create_client_from_env',
    'EXTRACT_NMCK_PROMPT',
    'EXTRACT_DEADLINE_PROMPT',
    'EXTRACT_GUARANTEE_PROMPT',
    'EXTRACT_REQUIREMENTS_PROMPT',
    'VERIFICATION_PROMPT',
    'format_extraction_prompt',
    'format_verification_prompt'
]
