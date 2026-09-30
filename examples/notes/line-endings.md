---
name: Mixed line endings
description: Some files mix CRLF and LF on purpose; patches must keep that.
type: feedback
---
Several files in this repository mix CRLF and LF line endings. Tools that
parse them expect exactly that. Never normalize line endings when patching
such a file: a whole-file rewrite with LF only produced a diff of every line
and broke the parser.

Keep the existing endings. Compare the numstat with and without CR before
committing.
