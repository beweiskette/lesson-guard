---
name: Generated API client
description: The API client module is generated; edit the schema and rerun the generator.
type: feedback
---
Never edit files that start with a "generated, do not edit" header directly,
for example `src/api/client_gen.py`. They are produced by
`tools/generate_client.py` from `schema/api.yaml`.

Instead: change the schema or the generator, then run
`python tools/generate_client.py`. Direct edits are lost on the next run and
the review diff becomes unreadable.
