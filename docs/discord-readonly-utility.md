# Read-only Discord utility

Canonical source: `tools/discord_readonly.py`. A runnable copy is installed at `~/scripts/discord/discord_readonly.py`.

The CLI loads the active profile's `HERMES_HOME/.env` into Hermes secret scope; it never accepts or prints a token. All Discord operations are GET requests only.

```sh
~/scripts/discord/discord_readonly.py channels --guild "Nella's dungeon"
~/scripts/discord/discord_readonly.py channels --guild "Nella's dungeon" --refresh
~/scripts/discord/discord_readonly.py messages --guild "Nella's dungeon" --channel disorganized-todo --hours 24
~/scripts/discord/discord_readonly.py messages --guild "Nella's dungeon" --channel 1517078886049910835 --hours 6 --limit 50
```

Guild names match exactly, case-insensitively; omit `--guild` only if the bot is in exactly one guild. `messages` requires one channel name or ID present in the readable inventory and a positive `--hours` window. `--limit` defaults to 100 and accepts 1–100 (one Discord page).

The inventory is persisted per active Hermes profile at `$HERMES_HOME/cache/discord_readonly/channels.json` for 24 hours by default; cache files are written with owner-only permissions and atomically replaced. Use `--refresh` to force an API refresh or `--cache-ttl SECONDS` to change the TTL. `--cache-path PATH` can select another cache file. The Python API `list_readable_channels(guild_id, refresh=False, ttl_seconds=86400, cache_path=None)` follows the same behavior.
