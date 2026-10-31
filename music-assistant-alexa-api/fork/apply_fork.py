"""Build-time step that attaches the fork's extension to the upstream image.

It never replaces an upstream file. It makes exactly two small, checked edits
and stops the image build with a clear message if upstream changed shape:

1. adds the fork's Portuguese intents to the upstream pt-BR interaction model
   (intents with the same name use the fork's sample phrases);
2. points the add-on entrypoint at `fork_ext.wsgi:app`, which installs the
   extension and then loads the upstream app unchanged.

Usage: apply_fork.py <app-src-dir> <entrypoint.sh> <pt-BR.intents.json>
"""

import json
import sys
from pathlib import Path

ENTRYPOINT_TARGET = " app:app "
ENTRYPOINT_REPLACEMENT = " fork_ext.wsgi:app "


def merge_model(model_path, intents_path):
    model = json.loads(model_path.read_text(encoding="utf-8"))
    language = model["interactionModel"]["languageModel"]
    fork_intents = json.loads(intents_path.read_text(encoding="utf-8"))["intents"]
    by_name = {intent["name"]: intent for intent in language["intents"]}
    for intent in fork_intents:
        by_name[intent["name"]] = intent
    # Keep upstream's order first, then the fork's additions.
    ordered = [by_name.pop(intent["name"]) for intent in language["intents"]]
    ordered.extend(by_name.values())
    language["intents"] = ordered
    model_path.write_text(
        json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(fork_intents)


def patch_entrypoint(entrypoint_path):
    text = entrypoint_path.read_text(encoding="utf-8")
    if text.count(ENTRYPOINT_TARGET) != 1:
        raise SystemExit(
            f"entrypoint: expected exactly one '{ENTRYPOINT_TARGET.strip()}' "
            "in the gunicorn command; upstream changed it - update apply_fork.py")
    entrypoint_path.write_text(
        text.replace(ENTRYPOINT_TARGET, ENTRYPOINT_REPLACEMENT), encoding="utf-8")


def main(argv):
    src, entrypoint, intents = (Path(arg) for arg in argv[1:4])
    count = merge_model(src / "models" / "pt-BR.json", intents)
    patch_entrypoint(entrypoint)
    if not (src / "fork_ext" / "wsgi.py").exists():
        raise SystemExit("fork_ext was not copied into the application directory")
    print(f"fork applied: {count} intents merged, entrypoint patched")


if __name__ == "__main__":
    main(sys.argv)
