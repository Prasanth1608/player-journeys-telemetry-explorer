// Tests for the pure helpers. Run with `npm test`.
import { describe, expect, it } from "vitest";
import { filterMatches } from "./filterMatches.js";
import { formatDuration, mapLabel, matchSummary, plural } from "./format.js";

const match = (id, map, date, extra = {}) => ({
  id, map, date, duration: 300, humans: 1, bots: 0,
  counts: { kills: 0, deaths: 0, loot: 0, storm: 0 },
  ...extra,
});

describe("format", () => {
  it("formats durations as m:ss", () => {
    expect(formatDuration(372)).toBe("6:12");
    expect(formatDuration(5)).toBe("0:05");
  });
  it("splits map ids into words", () => {
    expect(mapLabel("SaltmarshHollow")).toBe("Saltmarsh Hollow");
    expect(mapLabel("PylonYard")).toBe("Pylon Yard");
  });
  it("pluralises", () => {
    expect(plural(1, "bot", "bots")).toBe("1 bot");
    expect(plural(0, "bot", "bots")).toBe("0 bots");
    expect(plural(70412, "sample", "samples")).toBe("70,412 samples");
  });
  it("summarises a match", () => {
    const m = match("fbbc5d02-dd79", "SaltmarshHollow", "March_03", { duration: 512, humans: 2, bots: 14 });
    expect(matchSummary(m, "Mar 3")).toBe("Match fbbc5d02 · Mar 3 · 8m 32s · 2 humans, 14 bot paths");
  });
});

describe("filterMatches", () => {
  const all = [
    match("a", "SaltmarshHollow", "March_03", { bots: 3 }),
    match("b", "SaltmarshHollow", "March_04", { bots: 9 }),
    match("c", "PylonYard", "March_03", { bots: 20 }),
  ];

  it("keeps only the selected map and days", () => {
    const out = filterMatches(all, { mapId: "SaltmarshHollow", dates: new Set(["March_03"]), sortBy: "players" });
    expect(out.map((m) => m.id)).toEqual(["a"]);
  });
  it("sorts by most players", () => {
    const out = filterMatches(all, {
      mapId: "SaltmarshHollow", dates: new Set(["March_03", "March_04"]), sortBy: "players",
    });
    expect(out.map((m) => m.id)).toEqual(["b", "a"]);
  });
  it("returns nothing when no days are selected", () => {
    expect(filterMatches(all, { mapId: "SaltmarshHollow", dates: new Set(), sortBy: "players" })).toEqual([]);
  });
  it("does not change the original list", () => {
    const before = all.map((m) => m.id);
    filterMatches(all, { mapId: "SaltmarshHollow", dates: new Set(["March_03", "March_04"]), sortBy: "players" });
    expect(all.map((m) => m.id)).toEqual(before);
  });
});

describe("filterMatches: sorting and search", () => {
  const m = (id, date, start, loot) => ({ id, map: "M", date, start, duration: 100, humans: 1, bots: 0,
    counts: { kills: 0, deaths: 0, loot, storm: 0 } });
  const all = [m("aaaa-1111", "D1", 10, 5), m("bbbb-2222", "D2", 30, 1), m("b355-0292", "D2", 20, 9)];
  const days = new Set(["D1"]);
  it("sorts newest, oldest and by loot", () => {
    const both = new Set(["D1", "D2"]);
    expect(filterMatches(all, { mapId: "M", dates: both, sortBy: "newest" }).map((x) => x.start)).toEqual([30, 20, 10]);
    expect(filterMatches(all, { mapId: "M", dates: both, sortBy: "oldest" }).map((x) => x.start)).toEqual([10, 20, 30]);
    expect(filterMatches(all, { mapId: "M", dates: both, sortBy: "loot" })[0].id).toBe("b355-0292");
  });
  it("search ignores case and dashes, and looks across all days", () => {
    const out = filterMatches(all, { mapId: "M", dates: days, sortBy: "players", query: "B3550" });
    expect(out.map((x) => x.id)).toEqual(["b355-0292"]);
  });
  it("search matches from the start of the ID, not the middle", () => {
    const q = (query) => filterMatches(all, { mapId: "M", dates: days, sortBy: "players", query }).map((x) => x.id);
    expect(q("b").sort()).toEqual(["b355-0292", "bbbb-2222"]);
    expect(q("b").every((id) => id.startsWith("b"))).toBe(true);
    expect(q("1111")).toEqual([]);   // only in the middle of "aaaa-1111"
  });
});

import { formatLength, formatStartUtc } from "./format.js";
describe("formatLength", () => {
  it("shows match length as minutes and seconds", () => {
    expect(formatLength(518)).toBe("8m 38s");
    expect(formatLength(42)).toBe("42s");
  });
});

describe("formatStartUtc", () => {
  it("shows the start time in UTC, whatever the viewer's timezone", () => {
    expect(formatStartUtc(Date.UTC(2025, 1, 10, 8, 38) / 1000)).toBe("08:38 UTC");
    expect(formatStartUtc(Date.UTC(2025, 1, 10, 23, 5) / 1000)).toBe("23:05 UTC");
  });
});
