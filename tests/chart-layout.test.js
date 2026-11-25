const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const context = { globalThis: {}, console };
vm.createContext(context);
vm.runInContext(fs.readFileSync("static/chart-layout.js", "utf8"), context);
const layout = context.globalThis.chartLayout;

function boxesOverlap(ids, candidates) {
  const chosen = candidates.filter((item) => ids.includes(item.id));
  for (let i = 0; i < chosen.length; i += 1) {
    for (let j = i + 1; j < chosen.length; j += 1) {
      if (layout.intersects(chosen[i].box, chosen[j].box, 3)) return true;
    }
  }
  return false;
}

const font = 15;
const width = layout.textWidth("2,997", font);
const start = layout.boxFor(40, 40, width, 18, "middle");
const neighbor = layout.boxFor(58, 36, width, 18, "middle");
const peakFar = layout.boxFor(320, 80, width, 18, "middle");
const end = layout.boxFor(600, 120, width, 18, "middle");
const candidates = [
  { id: 0, priority: 80, box: start },
  { id: 1, priority: 100, box: neighbor },
  { id: 2, priority: 60, box: peakFar },
  { id: 3, priority: 80, box: end },
];
const ids = layout.chooseLabels(candidates);
assert.ok(ids.includes(1), "peak next to the start stays");
assert.ok(!ids.includes(0), "the overlapping start label is dropped");
assert.ok(ids.includes(2) && ids.includes(3), "well separated labels stay");
assert.strictEqual(boxesOverlap(ids, candidates), false);

const narrowWidth = 280;
const narrow = [];
for (let index = 0; index < 24; index += 1) {
  const x = 20 + (index / 23) * narrowWidth;
  narrow.push({ id: index, priority: index === 1 ? 100 : 60, box: layout.boxFor(x, 40, layout.textWidth("12,345", font), 18, "middle") });
}
const narrowIds = layout.chooseLabels(narrow);
assert.strictEqual(boxesOverlap(narrowIds, narrow), false);
assert.ok(narrowIds.length >= 2);
assert.ok(narrowIds.length < narrow.length);

const samples = [
  { x: 40, count: 2993 },
  { x: 70, count: 100 },
  { x: 220, count: 2819 },
];
const between = layout.nearestByX(samples, 55);
assert.strictEqual(between.count, 2993);
assert.notStrictEqual((samples[0].count + samples[1].count) / 2, between.count);
const onAxis = layout.nearestByX(samples, 200);
assert.strictEqual(onAxis.count, 2819);

const times = ["21:59:23", "22:00:32", "22:00:40", "22:02:50"].map((text, index) => ({
  id: index,
  priority: index === 0 || index === 3 ? 100 : 50,
  box: layout.boxFor(40 + index * 36, 260, layout.textWidth(text, 14), 16, "middle"),
}));
const timeIds = layout.chooseLabels(times);
assert.strictEqual(boxesOverlap(timeIds, times), false);
assert.ok(timeIds.includes(0) && timeIds.includes(3));

console.log("chart layout tests ok", { labels: ids, narrow: narrowIds.length, times: timeIds });
