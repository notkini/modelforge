import logging
import re
from dataclasses import dataclass
from typing import Optional
from .config import get_settings
from .interpreter import ParseError, parse_prompt
from .schemas import Spec

@dataclass
class InterpretationResult:
    spec: Optional[Spec]
    provider: str
    explanation: str
    needs_clarification: bool = False
    clarifying_question: Optional[str] = None
    fallback_reason: Optional[str] = None

class UnsupportedGeometry(ValueError): pass
logger=logging.getLogger(__name__)

def _gemini_failure_category(exc: Exception) -> str:
    """Return a safe category for logs and diagnostics; never include exception text."""
    code=getattr(exc,'code',None)
    status=getattr(exc,'status',None)
    if code==429 or status=='RESOURCE_EXHAUSTED': return 'quota_or_rate_limit'
    if code in (401,403) or status in ('UNAUTHENTICATED','PERMISSION_DENIED'): return 'authentication_or_permission'
    if code==404 or status=='NOT_FOUND': return 'model_not_found'
    if code==504 or status=='DEADLINE_EXCEEDED' or isinstance(exc,TimeoutError) or 'timeout' in type(exc).__name__.lower(): return 'timeout'
    if code==400 or status=='INVALID_ARGUMENT': return 'invalid_request'
    if isinstance(code,int) and code>=500: return 'provider_server_error'
    if isinstance(exc,(ConnectionError,OSError)) or type(exc).__name__ in ('ConnectError','ConnectTimeout','NetworkError'):
        return 'network_error'
    return 'request_failed'

def _gemini_failure_reason(exc: Exception) -> str:
    return {
        'structured_response_invalid':'Gemini returned data that did not pass geometry schema validation.',
        'quota_or_rate_limit':'Gemini quota or rate limit was reached.',
        'authentication_or_permission':'Gemini rejected the configured API key or project access.',
        'model_not_found':'The configured Gemini model was not found or is not enabled for this API key.',
        'timeout':'Gemini timed out before completing the request.',
        'invalid_request':'Gemini rejected the structured interpretation request as invalid.',
        'provider_server_error':'Gemini is temporarily unavailable.',
        'network_error':'Could not connect to Gemini; check server network access.',
    }.get(_gemini_failure_category(exc),'Gemini request failed.')

# Prompts that contain these keywords may describe geometry the local parser
# cannot build. In Auto mode they can be routed to Gemini. In explicit Local
# mode they are rejected with a helpful message.
_GEMINI_ELIGIBLE_TERMS = (
    'mounting hole', 'mounting holes', 'holes', ' hole',
    'boolean', 'intersection', 'union', 'difference',
    'fillet', 'chamfer',
)

# Prompts that are structurally invalid regardless of interpreter.
# These should never be forwarded to Gemini.
_ALWAYS_INVALID_PATTERNS = [
    re.compile(r'(?<!\w)-\s*\d+(?:\.\d+)?\s*mm'),   # negative dimensions: "-20 mm"
]


def _is_clearly_invalid(prompt: str) -> bool:
    """Return True if the prompt contains structurally invalid geometry intent."""
    for pattern in _ALWAYS_INVALID_PATTERNS:
        if pattern.search(prompt):
            return True
    return False


def _local(prompt: str, previous: Optional[Spec] = None) -> InterpretationResult:
    text=prompt.lower()
    if any(term in text for term in _GEMINI_ELIGIBLE_TERMS):
        raise UnsupportedGeometry(
            'This request needs explicit feature parameters (such as hole positions or fillet size). '
            'Configure Gemini or provide those values in the model parameters.'
        )
    if previous is not None:
        revised=_refine_local(prompt, previous)
        if revised:
            return InterpretationResult(revised,'local-deterministic','Refinement parameters parsed locally.')
    spec=parse_prompt(prompt)
    return InterpretationResult(spec,'local-deterministic','Geometry parameters parsed locally.')

def _refine_local(prompt: str, old: Spec) -> Optional[Spec]:
    text=prompt.lower()
    m=re.search(r'(?:make it|increase(?: the)? height(?: by)?|add)\s+(\d+(?:\.\d+)?)\s*mm\s*(?:taller|higher)',text)
    if m:
        dims=old.dimensions_mm.model_copy(update={'height':old.dimensions_mm.height+float(m.group(1))})
        return old.model_copy(update={'dimensions_mm':dims})
    m=re.search(r'(?:change|set|increase)?\s*(?:the )?walls?(?: thickness)?\s*(?:to|=)?\s*(\d+(?:\.\d+)?)\s*mm',text)
    if m:
        return old.model_copy(update={'wall_thickness':float(m.group(1))})
    m=re.search(r'(?:make it|make the model|make it)\s+(\d+(?:\.\d+)?)\s*mm\s+wider',text)
    if m:
        dims=old.dimensions_mm.model_copy(update={'width':float(m.group(1))})
        return old.model_copy(update={'dimensions_mm':dims})
    return None

def interpret(prompt: str, previous: Optional[Spec]=None, recent_context: Optional[list[dict]]=None, interpreter: Optional[str]=None) -> InterpretationResult:
    """Local deterministic interpretation with optional Auto→Gemini routing.

    Interpreter selection:
    - ``local``: local parser only; never calls Gemini.
    - ``gemini``: rejected here — Gemini script generation lives in codegen.
    - ``auto`` (default): tries local first; if it raises UnsupportedGeometry
      and Gemini is configured, signals the caller to route to Gemini via
      a ParseError with code ``needs_gemini``.  If Gemini is not configured,
      returns the local result or propagates the local error.

    For prompts that are structurally invalid (e.g. negative dimensions),
    Auto mode still rejects them locally instead of routing to Gemini.
    """
    settings=get_settings()
    model=settings['gemini_model']
    choice=interpreter or 'auto'
    if choice not in ('auto','gemini','local'):
        raise ParseError("Unrecognized interpreter selection: use 'auto', 'gemini', or 'local'.")
    if choice=='gemini':
        if not settings['gemini_api_key']:
            raise ParseError('Gemini interpretation is selected but GEMINI_API_KEY is not configured on the server.')
        raise ParseError('Gemini now generates CadQuery code directly for /models/generate and /refine; the structured-prompt service only interprets with the local deterministic parser.')

    # Explicit local: never route to Gemini
    if choice=='local':
        result=_local(prompt,previous)
        result.fallback_reason=None
        logger.info('Prompt interpretation complete provider=local-deterministic model=%s request_duration_ms=0 failure_category=none',model)
        return result

    # Auto mode: try local first
    try:
        result=_local(prompt,previous)
        if not settings['gemini_api_key']:
            result.fallback_reason='Gemini API key is not configured; interpreted with the local deterministic parser.'
            logger.info('Prompt interpretation complete provider=local-deterministic model=%s request_duration_ms=0 failure_category=missing_api_key',model)
        else:
            result.fallback_reason=None
            logger.info('Prompt interpretation complete provider=local-deterministic model=%s request_duration_ms=0 failure_category=none',model)
        return result
    except UnsupportedGeometry as exc:
        # Local cannot handle it; route to Gemini if available and the
        # request is not structurally invalid.
        if settings['gemini_api_key'] and not _is_clearly_invalid(prompt):
            logger.info('Auto mode: local parser rejected prompt, routing to Gemini. reason=%s',str(exc)[:120])
            raise _GeminiRoutingSignal(str(exc)) from exc
        # No Gemini key, or clearly invalid: surface the local error.
        raise
    except ParseError:
        # Local parser failed with a dimension error, not an unsupported-
        # geometry error: surface it directly regardless of Gemini config.
        raise


class _GeminiRoutingSignal(RuntimeError):
    """Internal signal: Auto mode wants the API layer to call Gemini."""
    pass
