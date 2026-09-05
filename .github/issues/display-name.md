## Problem

`--name` is a filesystem slug (`chattanooga`, `chatt_x13`, `test3`). The maps repo folder
convention is `{City name} - {hash}`, which wants a human name.

## Proposal

- Add `--display-name` to `make_map.py`, stored in the manifest as `display_name`.
- When absent, reverse-geocode the map centre with Nominatim
  (`https://nominatim.openstreetmap.org/reverse`, no key, 1 req/s, must send a User-Agent) and
  use `city` / `town` / `village` / `county`, in that order. Cache the response in `data/raw/`
  like every other fetch.
- Fall back to the capitalised slug when geocoding returns nothing or the network is down;
  never block a build on this.
- `publish` (#1) uses `display_name` for the folder; until this lands it capitalises the slug.

Depends on #1.
