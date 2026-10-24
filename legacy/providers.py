"""Replaceable prompt interpretation boundary for a future text model provider."""
from typing import Protocol
from .schemas import Spec
from .interpreter import parse_prompt

class PromptInterpreter(Protocol):
    def interpret(self, prompt: str) -> Spec: ...

class LocalPromptInterpreter:
    """Deterministic local parser; performs no AI inference or network calls."""
    def interpret(self, prompt: str) -> Spec:
        return parse_prompt(prompt)

interpreter: PromptInterpreter = LocalPromptInterpreter()
