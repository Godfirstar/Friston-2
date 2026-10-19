"""Shared, dependency-free text utilities for the lore wiki graph."""
import re
import unicodedata


def normalize(text):
    value = unicodedata.normalize('NFKC', str(text or '')).casefold()
    return re.sub(r'[\s\-_.·•:：,，。？！!?()（）“”‘’\[\]<>]+', '', value)


def tokenize(text):
    tokens = []
    for term in re.findall(r'[\u3400-\u9fff]+|[a-z0-9_]+', unicodedata.normalize('NFKC', text).casefold()):
        if '\u3400' <= term[0] <= '\u9fff':
            tokens.extend(term[i:i+2] for i in range(len(term)-1))
        elif len(term) >= 2:
            tokens.append(term)
    return ' '.join(tokens)


def phrase_query(text):
    return ' AND '.join('"' + word.replace('"', '""') + '"'
                        for word in dict.fromkeys(tokenize(text).split()))


def plain_text(text):
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'^\s*[-*]\s+', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def chunks(text, max_chars=420):
    """Retain complete sentences; a long indivisible sentence stays intact."""
    part = ''
    for sentence in re.findall(r'[^。！？!?；;]+[。！？!?；;]*', plain_text(text)):
        if part and len(part) + len(sentence) > max_chars:
            yield part.strip()
            part = ''
        part += sentence
    if part.strip():
        yield part.strip()
