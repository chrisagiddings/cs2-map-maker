"""Cities: Skylines II heightmap contract.

Every number here is a hard requirement of the CS2 map editor importer, verified
against Colossal Order's Modding Dev Diary #2 and the community maps wiki
(see CLAUDE.md "CS2 hard spec"). Nothing site-specific belongs in this file.
"""

# --- Image contract ---------------------------------------------------------
HEIGHTMAP_SIZE = 4096            # px, both axes, exactly
HEIGHTMAP_DTYPE = "uint16"       # 16-bit single-channel grayscale
HEIGHTMAP_MAX = 65535            # pixel value that maps to `height_scale` metres
HEIGHTMAP_MIN = 0                # pixel value that maps to 0 m

# --- Ground footprint -------------------------------------------------------
PLAYABLE_SIZE_M = 14336.0        # playable area side length, metres
PLAYABLE_M_PER_PX = PLAYABLE_SIZE_M / HEIGHTMAP_SIZE    # 3.5 m
WORLD_SIZE_M = 57344.0           # world map (backdrop) side length, metres
WORLD_M_PER_PX = WORLD_SIZE_M / HEIGHTMAP_SIZE          # 14.0 m
WORLD_TO_PLAYABLE_RATIO = int(WORLD_SIZE_M / PLAYABLE_SIZE_M)   # 4
WORLD_CENTER_PX = HEIGHTMAP_SIZE // WORLD_TO_PLAYABLE_RATIO     # 1024 px

# --- Height scale (typed into the editor) -----------------------------------
# The editor exposes a single "height scale" number: pixel 65535 == that many metres.
# Range/default below come from the project brief and are NOT confirmed by any
# primary source found so far (one community tool cites 4096 m as the default).
# Treat them as advisory bounds for the recommended value we print, not as a
# hard contract.
HEIGHT_SCALE_MIN_M = 200.0
HEIGHT_SCALE_MAX_M = 10000.0
HEIGHT_SCALE_DEFAULT_M = 4000.0

# --- Resource masks ---------------------------------------------------------
RESOURCE_MAP_SIZE = 256          # px, grayscale; white = deposit present
RESOURCE_MAP_DTYPE = "uint8"

# Sanity: the footprint numbers must agree exactly.
assert abs(PLAYABLE_M_PER_PX - 3.5) < 1e-9
assert abs(WORLD_M_PER_PX - 14.0) < 1e-9
assert WORLD_TO_PLAYABLE_RATIO * PLAYABLE_SIZE_M == WORLD_SIZE_M
assert WORLD_CENTER_PX == 1024
