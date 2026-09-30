---
name: Image server /free endpoint
description: Calling /free on the shared image server unloads all models for everybody.
type: feedback
---
Do not call the `/free` endpoint of the shared image generation server. It
unloads every model from GPU memory, for all users and all running jobs, and
the next job waits minutes for a reload.

If memory is tight, wait for the queue to drain or ask the owner instead.
