# Real PDF regression fixtures

The two PDFs supplied in the issue are used locally but are not committed or pushed.
Place them in this directory under the names below, or set `PATENT_REAL_PDF_DIR`
to a directory containing them. Run from `tools/local_patent_dashboard`:

```sh
python -m unittest test_real_patents -v
```

Missing files are explicitly reported as skipped. The normal suite also includes
small deterministic regressions for reading order, heading classification, mixed
pages, claim dependencies and cover-page consistency checks.

| File | SHA-256 | Expected sections (PDF page numbers, starting at 1) |
| --- | --- | --- |
| CN119853429A.pdf | `3edbda29ca0004f2d223af51968cf5ef3950702daf1da9ace4394ee89a7c36e1` | cover 1; claims 2–4; description 5–17; drawings 18–28 |
| US10211630.pdf | `2bcd6525f8a8abc7ac418ac7b2d8a1eb693af76cfeec2b1bf2329c85acc94d0a` | bibliographic 1–2; drawings 3–9; description 10–20; claims 20–22 |

Ground truth checked against these PDFs:

- CN119853429A: 19 claims; independent claim 1; claim 19 continues on page 4.
- US10211630B1: 24 claims; independent claims 1, 12, 23 and 24.
  Page 20 starts as description, switches to claims in its left column, and
  continues claim 1 at the top of its right column. Claim 12 crosses to page 21;
  claim 24 crosses to page 22. `DESCRIPTION OF DRAWINGS` on page 12 belongs to
  the specification, not the drawing sheets.
- Component 10 (第一基板) has drawing matches in CN figures; component 104
  (medium voltage transformers) has a drawing match on US page 3.

The end-to-end tests upload the actual PDFs through the HTTP API, await extraction,
verify all page groups, claim numbers, independent/dependent references, cross-page
continuations, metadata and figure matches. Results are written to the ignored
`outputs/real-patent-verification` directory. No production ledger is accessed.
