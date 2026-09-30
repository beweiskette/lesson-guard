---
name: No API keys in shipped packages
description: Keys stay in the local .env, never in files or archives given to third parties.
type: feedback
---
Never put API keys or tokens into files that end up in a package for someone
else: no keys in `dist/`, in config files, or in zip archives. Keys live only
in the local `.env`, which is never packed.

Read keys from the environment instead (`os.environ["API_KEY"]`) and ship an
`.env.example` with placeholders.
