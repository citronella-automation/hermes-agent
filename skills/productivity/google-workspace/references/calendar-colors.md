# Calendar label/color verification

Authoritative guide: https://developers.google.com/workspace/calendar/api/guides/labels (read 2026-10-13).

1. GET `calendars.get(primary)` and inspect `labelProperties.eventLabels`. Labels expose `id`, `backgroundColor`, and optional `name`. Exact names/IDs only; never infer names from nearest hex or conflate the legacy `colors.get` palette with modern labels.
2. Use `eventLabelId` with `eventLabelVersion=1` on insert/patch and readback. Bundled discovery lacks the parameter; append it to SDK HttpRequest URI before execute, preserving managed authentication. Verify returned label ID.
3. With user authorization, unresolved category labels fall back to verified legacy IDs: cs3210=9, cs1231s=10, admin=8, research=2, lab=10, social=3, health=11. Report fallback explicitly and omit eventLabelVersion so colorId is processed. Cocoa has no equivalent and fails clearly. Duplicate names fail; auth/network errors propagate.
4. `--label-name` and `--event-label-id` are exact, validated explicit overrides without fallback; all selectors are mutually exclusive with `--category` and legacy `--color-id`. Unspecified updates must preserve color/label.
5. Verification is GET-only unless the user separately authorizes event mutations. Construct (but do not execute) a modern SDK request to verify body serialization and query support when discovery lags. Do not define/rename labels to make a mapping work. Keep mocked verification distinct from a live write.

## Live evidence and unresolved conventions

2026-10-13 primary Theodore returned 24 labels; all names were omitted. Exact private ID/hex response is at `~/.hermes/cache/scratch/theodore-event-labels-2026-10-13.json`, intentionally outside Git; scratch may expire. Names Cobalt, Pistachio, Birch, Eucalyptus, Grape, Tomato and Cocoa are preferred conventions, not names actually returned in that response. Consequently all seven legacy-capable categories currently report fallback, and extracurricular requires exact label ID/name confirmation. `lab` green remains ambiguous; health's existing red convention is not live-name verified. Do not invent a definitive semantic mapping.

For example metadata contains a label with backgroundColor #795548, but absence of name does not independently establish Cocoa identity. An explicit user-confirmed ID can select it. No event writes or calendar-label edits were performed during verification.
