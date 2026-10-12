"""One tiny Claude call to confirm the key and model work. Prints only PASS or FAIL; never the key."""
import sys

from .llm import ClaudeAssessor


def main():
    try:
        assessor = ClaudeAssessor()
        if not assessor.available:
            return False
        response = assessor.client.messages.create(
            model=assessor.config["model"], max_tokens=64,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}])
        return response.stop_reason in ("end_turn", "max_tokens") and bool(response.content)
    except Exception:
        return False


if __name__ == "__main__":
    ok = main()
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
