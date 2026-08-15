/**
 * 좌표 계산의 계약. 🔴 그림은 눈으로 보지만 **겹침은 산수로 막는다** —
 * 파트너 두 명이 같은 자리에 오는 것은 보기 전에 알 수 있다.
 *
 * 실행: `npm test` (Node 24 가 .ts 를 그대로 돈다 — 테스트 러너를 새로 깔지 않는다)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { GEOMETRY, bandHeight, clip, hubRadius, layoutHubs, selectHubs, type HubNode } from "./hubLayout.ts";

function hub(name: string, count: number, partners: string[]): HubNode {
  return { name, canonical: name, kind: "public", count, partners };
}

test("반지름은 넓이가 건수에 비례한다 — 지름 비례가 아니다", () => {
  // 넓이 비 ≈ 건수 비. 바닥값 14 를 뺀 부분만 보면 정확히 sqrt 다.
  const a = hubRadius(9) - 14;
  const b = hubRadius(36) - 14;
  assert.equal(Math.round((b / a) * 100), 200); // sqrt(36/9) = 2
});

test("건수가 0이어도 원이 사라지지 않는다", () => {
  assert.equal(hubRadius(0), 14);
});

test("파트너가 겹치지 않는다 — 간격이 rowGap 밑으로 안 내려간다", () => {
  const layout = layoutHubs([hub("방위사업청", 91, ["가", "나", "다", "라", "마", "바"])]);
  const ys = layout.hubs[0].partners.map((p) => p.y);
  for (let i = 1; i < ys.length; i += 1) {
    assert.ok(ys[i] - ys[i - 1] >= GEOMETRY.rowGap - 0.001, `${i}번째가 붙었다`);
  }
});

test("허브의 띠가 서로 침범하지 않는다 — 파트너 6명과 1명이 섞여도", () => {
  const layout = layoutHubs([
    hub("방위사업청", 91, ["가", "나", "다", "라", "마", "바"]),
    hub("삼성전자(주)", 66, ["사", "아"]),
    hub("한국수력원자력(주)", 35, ["자", "차", "카", "타", "파", "하"]),
  ]);
  const all = layout.hubs.flatMap((h) => h.partners.map((p) => p.y)).sort((a, b) => a - b);
  for (let i = 1; i < all.length; i += 1) {
    assert.ok(all[i] - all[i - 1] >= GEOMETRY.rowGap - 0.001, "다른 허브의 파트너와 겹쳤다");
  }
});

test("파트너 하나면 허브와 같은 높이에 온다", () => {
  const layout = layoutHubs([hub("SATORP", 13, ["삼성E&A"])]);
  const [placed] = layout.hubs;
  assert.equal(placed.partners[0].y, placed.y);
});

test("모든 것이 도면 안에 들어온다", () => {
  const layout = layoutHubs([
    hub("가", 91, ["a", "b", "c", "d", "e", "f"]),
    hub("나", 5, ["g"]),
  ]);
  for (const h of layout.hubs) {
    assert.ok(h.y - h.r > 0, "허브가 위로 잘렸다");
    assert.ok(h.y + h.r < layout.height, "허브가 아래로 잘렸다");
    for (const p of h.partners) {
      assert.ok(p.y - p.r > 0 && p.y + p.r < layout.height, "파트너가 잘렸다");
      assert.ok(p.x + p.r < layout.width, "파트너가 오른쪽으로 넘쳤다");
    }
  }
});

test("띠 높이는 파트너 수를 따라 늘어난다", () => {
  assert.ok(bandHeight(6) > bandHeight(2));
  assert.equal(bandHeight(1), GEOMETRY.minBand); // 하나뿐이어도 최소 높이는 지킨다
});

test("랜딩은 파트너가 둘 이상인 큰 허브만 고른다", () => {
  const chosen = selectHubs([
    hub("큰 허브", 91, ["a", "b", "c"]),
    hub("선 하나", 13, ["a"]), // 🔴 count 는 크지만 파트너가 하나 — 방사형이 아니다
    hub("작은 허브", 2, ["a", "b"]),
  ]);
  assert.deepEqual(chosen.map((h) => h.name), ["큰 허브"]);
});

test("고른 허브는 건수 내림차순이다", () => {
  const chosen = selectHubs([
    hub("중", 35, ["a", "b"]),
    hub("대", 91, ["a", "b"]),
    hub("소", 6, ["a", "b"]),
  ]);
  assert.deepEqual(chosen.map((h) => h.count), [91, 35, 6]);
});

test("긴 이름은 자르고 짧은 이름은 그대로 둔다", () => {
  assert.equal(clip("한화에어로스페이스", 20), "한화에어로스페이스");
  assert.equal(clip("General Electricity Company of Libya", 10), "General E…");
});
