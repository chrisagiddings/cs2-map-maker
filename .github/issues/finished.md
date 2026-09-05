## Goal

The pipeline stops at the heightmap. The real deliverable is the map *after* the editor
work: import, water sources placed, outside connections, resources, name and thumbnail, saved.
Add a command that captures that finished map from CS2 and commits it into the map's folder
in `cs2-map-maker-maps`, next to the heightmaps it was built from.

```
python publish_map.py --name chattanooga --finished                    # find the CS2 save, commit it
python publish_map.py --name chattanooga --finished "Chattanooga v2"   # explicit map name in CS2
```

## Where CS2 keeps finished maps

`%USERPROFILE%\AppData\LocalLow\Colossal Order\Cities Skylines II\Maps\<MapName>.cok`
plus a sidecar `<MapName>.cok.cid`. Both are required for the game to load the map. The `.cok`
is the map itself (terrain, water sources, outside connections, resources, prefabs, thumbnail);
verify the exact container format before parsing anything.

## What gets committed

```
Chattanooga - 109bf4e/
├── ...                            # heightmaps, QA, manifest (already there from publish)
└── finished/
    ├── Chattanooga.cok
    ├── Chattanooga.cok.cid
    ├── thumbnail.png              # extracted from the .cok if feasible, else a screenshot the user drops in
    └── finished.json              # editor session record, see below
```

`finished.json` records what was done in the editor, because the .cok is opaque to git:

```json
{"cs2_map_name": "Chattanooga", "cs2_version": "1.2.3f1", "saved": "2026-09-06T14:02:00",
 "source_hash": "109bf4e", "height_scale_m": 610, "sea_level_m": 63,
 "notes": "Border river N and S at 63 m, stream sources on Lookout Creek and S. Chickamauga, highway W/E",
 "placements_used": "Chattanooga_placements.json"}
```

Prompt for `notes` interactively (or `--notes`); everything else is derived.

## Behaviour

- Locate the `.cok` by `--finished <name>`; without a name, list the `Maps/` folder sorted by
  mtime and pick the one whose name matches the display name, else ask.
- Verify both files exist and the `.cok` is newer than the published heightmap; warn otherwise.
- Verify `source_hash` matches the folder the map is being committed into. If the heightmap
  folder does not exist yet, run the normal publish first.
- Copy, write `finished.json`, regenerate the maps README index with a "Finished" column
  (link to the `.cok`), commit `chattanooga 109bf4e: finished map "Chattanooga"`, push.
- `.cok` files are tens of MB; they belong under LFS (#2) alongside the PNGs.

## Import path for other people

Document in the maps README: copy `finished/*.cok` and `*.cok.cid` into the `Maps/` folder above
and the map appears in the editor and the new-game map list. Consider an `install_map.py` helper
that does exactly that from a maps-repo clone.

## Related

- #1 publish (folder convention), #2 LFS, #5 water-source hints, __EPIC__ placement guide
