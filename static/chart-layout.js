(function (root) {
  function textWidth(text, fontSize) {
    return Math.max(fontSize, String(text).length * fontSize * 0.62);
  }

  function boxFor(x, y, width, height, anchor) {
    let left = x - width / 2;
    if (anchor === "start") left = x;
    if (anchor === "end") left = x - width;
    return { left: left, right: left + width, top: y - height, bottom: y };
  }

  function bounds(box) {
    const left = box.left != null ? box.left : box.x;
    const top = box.top != null ? box.top : box.y;
    const right = box.right != null ? box.right : box.x + box.width;
    const bottom = box.bottom != null ? box.bottom : box.y + box.height;
    return { left: left, right: right, top: top, bottom: bottom };
  }

  function intersects(a, b, pad) {
    const first = bounds(a);
    const second = bounds(b);
    const gap = pad || 0;
    return first.left - gap < second.right && first.right + gap > second.left && first.top - gap < second.bottom && first.bottom + gap > second.top;
  }

  function chooseLabels(candidates) {
    const ordered = candidates.slice().sort(function (a, b) {
      return b.priority - a.priority || a.box.left - b.box.left;
    });
    const kept = [];
    const ids = [];
    ordered.forEach(function (item) {
      if (kept.some(function (other) { return intersects(item.box, other.box, 3); })) return;
      kept.push(item);
      ids.push(item.id);
    });
    return ids;
  }

  function nearestByX(points, x) {
    let best = points[0];
    let bestDistance = Infinity;
    points.forEach(function (point) {
      const distance = Math.abs(point.x - x);
      if (distance < bestDistance) {
        best = point;
        bestDistance = distance;
      }
    });
    return best;
  }

  root.chartLayout = {
    textWidth: textWidth,
    boxFor: boxFor,
    intersects: intersects,
    chooseLabels: chooseLabels,
    nearestByX: nearestByX,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
