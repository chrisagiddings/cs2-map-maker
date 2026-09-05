## Proposal

`make_map.py` computes the map hash (#1) from its parameters *before* running and checks the
maps repo (local clone if present, else the GitHub API contents listing) for an existing
`{name} - {hash}` folder. If found, print where it is and skip the build unless `--force`.
Saves a rebuild and, more importantly, stops accidental near-duplicate publishes.

Depends on #1.
