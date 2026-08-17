"""Hardware-free stand-in for a framed procedure child.

Frames prompts exactly the way ``omotion.scripts.framed_prompts`` does (the
sentinel is hardcoded to the SDK's value — the launch command must not carry
it, so the test can assert the raw sentinel never reaches the terminal), and
also holds a deliberately prompt-shaped partial line open longer than the
pane's quiet timer, so the end-to-end test can prove a framed child never
falsely arms the operator input. Exits 0 only if both scripted answers
arrive intact.
"""
import json
import sys
import time

SENTINEL = "@OW-PROMPT@ "

print("stub procedure starting")
sys.stdout.write("loading calibration table: ")  # prompt-shaped tail ...
sys.stdout.flush()
time.sleep(0.8)  # ... held open past the pane's 400 ms quiet interval
print("loaded")  # completes the line

print(SENTINEL + json.dumps(
    {"prompt": "Which sensor is installed? (left/right): "}), flush=True)
side = input()
print("side recorded: " + side)

print(SENTINEL + json.dumps({"prompt": "Proceed? (yes/no): "}), flush=True)
ok = input()
print("answer recorded: " + ok)

sys.exit(0 if (side, ok) == ("left", "yes") else 3)
