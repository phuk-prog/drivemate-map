# Improvements

## App (DriveMate-app)

1. Merge turns that are only a few metres apart in `js/routing.js`, so the second prompt is never skipped.
2. Speak street names in more prompts. Many turns say only "turn right".
3. Combine close back-to-back prompts, such as two bends 8 s apart, into one instruction.
4. Estimate position during GPS loss, such as in tunnels, instead of only warning.
5. Reroute faster after a wrong turn. It takes about 19 s now.
6. Add the headless drive simulator as an automatic test on every change. A drive fails if a turn prompt is missed or arrival is not announced.
7. Run simulated drives on the Pixel 8 emulator with fake GPS, to check the screen as well.
8. Test audio over Bluetooth and Android Auto, and navigation with the screen locked.

## Map (Drivemate-map)

1. Run all `tools/test-*.mjs` tests on every pull request and on every push to main.
2. Add tests for `mvt.mjs`, `osm-pbf.mjs`, `classes.mjs`, `roundabouts.mjs`, `grid.mjs`, `png.mjs` and `update-regions.mjs`.
3. Add write, read-back and validate tests with randomly generated map data, plus tests that give the reader damaged files.
4. Check that road-graph repair gives the same result whatever order the roads are read in.
5. Commit small sample `.dmap` files that the app's reader must also pass.
6. Fail the build when the road counts for a fixed small area change too much.
7. Mark roundabouts in all published maps, so the app can give exit numbers offline.
