def check(name, ok):
    """Named assertion, so a failing check reads as a sentence."""
    assert ok, name
