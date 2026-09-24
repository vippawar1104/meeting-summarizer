def defang(text: str) -> str:
    """Stop untrusted text from closing or forging our data delimiters."""
    return text.replace("<untrusted_", "<untrusted-").replace("</untrusted_", "</untrusted-")
