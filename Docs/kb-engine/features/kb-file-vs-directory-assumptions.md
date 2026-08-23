---
concept_id: engine.kb.file_vs_directory
evidence: measured
files:
  - .tools/index_code.py
  - .tools/add_note.py
keywords:
  - IsADirectoryError
  - TypeError
  - directory KB
  - split KB
  - add_note fails
  - cannot record a finding
  - no annotation file
  - literal tilde
  - annotations list
  - no backup
  - not rebuilt
  - stale index
  - No matches
  - freshness
  - file_state
  - snapshot_annotations
  - version overlay
kind: feature
name: kb-file-vs-directory-assumptions
status: resolved
---

## brief

FOUR BUGS IN ONE DAY SHARED ONE SHAPE: code written when a KB was a single JSON file, meeting a KB
that is a DIRECTORY. Each failed silently, or with an error naming the wrong thing. Fixed -- but
the PATTERN is the durable part, because anything else touching an annotation path should be read
with it in mind.

## notes

WHAT BROKE:

 1. NO BACKUPS. snapshot_annotations() called path.read_bytes(), which raises IsADirectoryError on a
    directory -- swallowed by the same `except OSError: continue` that means "an absent version
    overlay is normal". The SPLIT form, the one recommended for sharing, was the single form with no
    backup.

 2. THE VERSION OVERLAY WAS ORPHANED BY A MOVE. It is found RELATIVE to the KB, so moving the KB
    without it silently lost entries. Caught only by comparing index row counts.

 3. A KB-ONLY EDIT DID NOT TRIGGER A REBUILD. file_state() stats the path it is given, and for a
    directory that is the ROOT's mtime -- which changes when a subdirectory appears and at no other
    time. An entry lives two levels down, so adding, editing or deleting one moved nothing. The
    engine reported "fresh", skipped the rebuild, and the query answered `No matches` -- which reads
    as "nobody wrote that down", not "your index is behind".
    IT HID BECAUSE ANY SOURCE EDIT FORCES A FULL REBUILD and picks the KB up on the way past --
    including an edit to the engine itself, which is part of the source digest. Only a KB-ONLY
    rebuild, which is exactly what recording a finding does, exposes it.

 4. add_note.py COULD NOT WRITE AT ALL. Three faults at once: `annotations` as a LIST gave a
    TypeError before anything opened (REPO_ROOT / ["a","b"]); a root outside the repo became
    `<repo>/~/kb/project`, reported as "no annotation file" with a literal tilde in it; and the
    directory gave IsADirectoryError from read_text().

HOW TO CHECK FOR MORE:

    grep -n "annotation_path\|annotation_paths\|DEFAULT_ANNOTATIONS\|EXTRA_ANNOTATIONS" .tools/*.py

The dangerous calls treat a path as a file without saying so: read_text, read_bytes, stat,
with_name, open, and `REPO_ROOT / value` where value came from config. Two of the four were
swallowed by an `except OSError` written for a different reason, which is why they were silent.

*** AND THE CONFIG VALUE MAY BE A LIST *** pointing OUTSIDE the repository -- that is how the
public/local split works -- so anything reading it must handle a list and must expanduser().
