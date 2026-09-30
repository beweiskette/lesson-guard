---
name: Double-encoded UTF-8
description: Scripts were saved with mojibake after a round trip through cp1252.
type: feedback
---
A script was read as cp1252 and written back as UTF-8, so every umlaut turned
into mojibake ("GrÃ¼ezi" instead of "Grüezi"). Always read and write source
files as UTF-8 and check the text before saving.
