"""vr: on-device voice router + per-tree experts (training side, desktop only).

Picks a topic / tree node from recognizer word hypotheses. Never generates text:
every output is an index into signed-off, cited cards.
"""
