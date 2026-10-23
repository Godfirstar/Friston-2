"""One format boundary for spoken input; never correct words or infer intent."""
import re


_HAN = (r'\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
        r'\U00020000-\U0002fa1f\U00030000-\U000323af')
_HAN_TOKEN_SPACE = re.compile(r'(?<=[' + _HAN + r'])\s+(?=[' + _HAN + r'])')
_MEMORY_PREFIX = re.compile(r'^(?:记住|忘记)\s*[:：]')


def prepare_input_text(text, *, input_source='text'):
    """Retain the raw input and produce a format-normalized working copy.

    Some ASR decoders delimit Chinese tokens with spaces. Join only whitespace
    between two Han characters. English word boundaries, digits, punctuation,
    negation and the recognized characters themselves are preserved. Keyboard
    input is only stripped at its outer boundary, as before.
    """
    if not isinstance(text, str):
        raise TypeError('输入必须是文字')
    source = 'speech' if input_source == 'speech' else 'text'
    raw = text.strip()
    normalized = _HAN_TOKEN_SPACE.sub('', raw) if source == 'speech' else raw
    return {'input_source': source, 'raw_text': raw, 'normalized_text': normalized,
            'normalizations': ['han_token_whitespace'] if normalized != raw else []}


def is_memory_command(text):
    """Recognize the existing colon-delimited protocol, including its spacing."""
    return bool(_MEMORY_PREFIX.match(text))
