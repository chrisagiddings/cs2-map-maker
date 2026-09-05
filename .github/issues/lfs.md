## Problem

Each published map is two 4096² 16-bit PNGs, roughly 50–70 MB together. Committed as plain
blobs, `cs2-map-maker-maps` reaches GitHub's 1 GB soft limit after about 15 maps, and every
clone drags the full history of every heightmap.

## Proposal

Track the heightmaps with Git LFS in the maps repo:

```
git lfs install
git lfs track "*_heightmap.png" "*_worldmap.png"
```

Keep QA sheets (~1 MB) and manifests as normal blobs so the browsable part of the repo stays
cheap and renders in the GitHub UI.

- `publish` (#1) should detect whether LFS is initialised in the maps repo and warn (not fail)
  when it isn't.
- Git for Windows bundles `git-lfs`; verify it is on PATH before assuming.
- Document the LFS free-tier limits (1 GB storage, 1 GB/month bandwidth) in the maps README and
  what to do when they are hit (GitHub data packs, or the release-assets alternative below).

## Alternative

Publish a GitHub Release per map with the PNGs as assets and commit only manifest + QA sheet.
No LFS quota, but maps are no longer a plain `git clone` away.

Depends on #1.
