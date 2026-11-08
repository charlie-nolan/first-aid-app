# plb: Pocket Librarian Builder

The PC-side builder for the Pocket First-Aid Librarian. It turns public-domain manuals into cited fallback trees, a phrase list for ESP-SR and 1-bit pictures, as the input for the `library.bin` packer.

```
manual PDFs --ingest--> sources/ (sha256 pinned in sources.yaml)
            --extract-> build/extract/ID/{sections.json, figures.json, figures/*.png}
content/trees/*.yaml + sections --build--> build/intermediate/   (docs/INTERMEDIATE.md)
            --test--> every node sourced, every passage verbatim, every phrase lands right
            --pack--> library.bin   (/mnt/project-files/romkit: plb pack --packer "python3 romkit/pack.py")
            --test --reader-cmd cartcli--> phrases + passages re-checked through the real C reader
```

## Use
```
pip install -e '.[test]'
plb ingest ATP3-50.21 ~/Downloads/atp3-50-21.pdf   # or: plb fetch (if the hosts are reachable)
plb extract ATP3-50.21
plb grep tourniquet
plb validate                  # schema check of content/ only (fast, no PDFs)
plb build && plb test
# packing needs the romkit deps too: pip install numpy Pillow freetype-py uharfbuzz
plb pack --packer "python3 /mnt/project-files/romkit/pack.py"
pytest -q
```

## Figures via Marker (cyberdeck extractor)
On the GPU box with `marker_env` active:
```
plb extract ATP3-50.21 --figure-cmd "python -m plb.marker"
# or reuse an earlier Marker run (turn off BINARIZE_LINE_ART first so plb gets greyscale):
plb extract ATP3-50.21 --figure-cmd "python -m plb.marker --marker-out ~/subaru_cyberdeck/test_batch_out"
```
Marker images are matched to "Figure X-Y." captions in its markdown. Images with no caption are kept as `ID/img/p<page>-<n>` and cited by page. Passage text always comes from plb's direct PDF text, never from Marker.

## Schemas and CI
`plb/schema.py` holds Pydantic schemas for `content/trees/*.yaml`, `config.yaml`, `contexts.yaml`, `strings.yaml` and `phrases/expect.yaml`. They catch shape mistakes (a misspelt key like `cites:` or `choises:`, a wrong type, a choice to a missing node, a bad citation form) before anything is built. `plb build`/`plb test` run them too.

GitHub Actions (`.github/workflows/ci.yml`) runs on every PR: `plb validate` on the committed content, these tests (including a fixture manual packed into a real `library.bin` and read back through the C reader), and the romkit reader tests. CI never needs the manual PDFs or personal-only sources.

## Rules the builder enforces
- No text of our own on screen: passages are verbatim from a pinned manual. The only exceptions are navigation labels (at most 40 chars) and `content/strings.yaml`.
- Every node cites manual + paragraph, and every kit/improvised branch has a "nothing" fallback.
- Manuals must say "Approved for public release". Figures captioned as copyrighted or "used with permission" are refused.
- Plant shelf: no mushrooms or fungi. Leaves are a species page with look-alikes, or "no match".

Sources and download links: `SOURCES.md`. Licence: GPL-3.0-or-later.
