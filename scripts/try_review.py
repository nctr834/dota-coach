"""Run the match-review agent against a match and print the review + tool trace.

  python3 scripts/try_review.py <match_id> [account_id]
  python3 scripts/try_review.py --account <account_id>   # picks a recent match
  python3 scripts/try_review.py <match_id> [account_id] --tool-trace  # full results
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analyzer"))
os.chdir(ROOT)

from match_review import review_match, AGENT_MODEL


def main(argv):
    show_results = "--tool-trace" in argv
    argv = [a for a in argv if a != "--tool-trace"]

    account_id = None
    match_id = None
    if not argv:
        print(__doc__)
        return 1
    if argv[0] == "--account":
        account_id = int(argv[1])
    else:
        match_id = int(argv[0])
        if len(argv) > 1:
            account_id = int(argv[1])

    print(f"model: {AGENT_MODEL}")
    print(f"match_id={match_id} account_id={account_id}\n")

    result = review_match(account_id=account_id, match_id=match_id)

    print("=== tool trace (the agent's path) ===")
    for i, step in enumerate(result["tool_trace"], 1):
        print(f"  {i}. {step['tool']}({step['input']})")
        if show_results:
            for line in json.dumps(step.get("result", {}), indent=2).splitlines():
                print(f"     {line}")

    print("\n=== review ===")
    print(result["review"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
