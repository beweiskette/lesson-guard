---
name: Backup folders break class registration
description: Backup copies of scripts inside the project declare the same classes twice.
type: project
---
The engine scans the whole project folder. Backup folders inside it
(`backup_old/`, `scripts/backups/`) contain copies of scripts with the same
class declarations, which causes "class already declared" errors.

Never create backup copies of scripts inside the project tree. Put backups
outside the project or exclude the folder from scanning.
