# Fallback trees

One YAML file per topic. The schema and rules are in the docstring of `plb/trees.py`. The short version:

- A node's body is **only** cited manual text: a whole paragraph (`ATP3-50.21/2-15`) or a verbatim excerpt (`{ref, from, to}`). Labels are navigation, at most 40 chars.
- Branches go kit, then improvised, then nothing. If a node has kit or improvised branches, it must also have a nothing branch.
- On the plant shelf, every leaf is a species (with `lookalikes:` citations) or `no_match`. Mushrooms and fungi are rejected.
- Optional `tags: {region: [pnw, -id], season: [winter], activity: [water]}` on a tree or node. Untagged means it shows everywhere. Put `emergency: true` on a node that must never be filtered out.
- Give each node as many spoken variants as you can (`phrases:`, or `{say: ..., always: true}`). Each section gets its own 200-phrase voice list.
- Use `plb grep WORD...` and `plb show ID` to find paragraphs to cite. Run `plb test` before committing.

Charlie vets which sections ship. Trees are drafted from the extracted manuals only, never from memory.
