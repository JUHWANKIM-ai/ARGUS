/**
 * 허브 방사형 관계망의 **좌표 계산**. 🔴 여기에 그리기는 없다 — 순수 함수라 눈이 아니라
 * 테스트로 확인할 수 있다(`hubLayout.test.ts`).
 *
 * 왜 힘 기반 시뮬레이션이 아닌가: 우리 데이터의 실제 모양이 **쌍(A↔B)이 아니라
 * 발주처 → 수주사**다. 방위사업청 하나가 6개사에 일을 준다. 그 구조를 그대로 그리는 것이
 * 허브 방사형이고, 힘 시뮬레이션은 그 구조를 흩어 놓는다.
 */

export type HubNode = {
  name: string;
  canonical: string;
  kind: string;
  count: number;
  partners: string[];
};

export type PlacedPartner = {
  name: string;
  x: number;
  y: number;
  r: number;
};

export type PlacedHub = {
  hub: HubNode;
  x: number;
  y: number;
  r: number;
  partners: PlacedPartner[];
};

export type Layout = {
  width: number;
  height: number;
  hubs: PlacedHub[];
};

/** 도면의 뼈대 치수. viewBox 가 이 값을 그대로 쓴다. */
export const GEOMETRY = {
  // 왼쪽 185px 이 허브 이름, 오른쪽 305px 이 파트너 이름 자리다.
  // 🔴 `한국수력원자력` 이 왼쪽에 한 줄로 들어가야 한다 — 줄바꿈은 SVG 가 안 해 준다.
  width: 830,
  hubX: 230,
  partnerX: 500,
  /** 허브 하나가 차지하는 세로 띠의 최소 높이 — 파트너가 하나뿐이어도 이만큼은 쓴다. */
  minBand: 118,
  /** 파트너 한 명당 세로 간격. 이름이 13px 라 이보다 좁으면 글자가 붙는다. */
  rowGap: 44,
  padTop: 24,
  padBottom: 24,
  partnerRadius: 9,
} as const;

/**
 * 🔴 반지름은 sqrt 로 — 원의 **넓이**가 건수에 비례해야 눈이 속지 않는다.
 * 지름에 비례시키면 91건짜리가 5건짜리보다 18배 커 보인다(실제로는 넓이 18배가 맞다).
 * 14 는 바닥값이다 — 건수가 적어도 글자를 담을 원은 돼야 한다.
 */
export function hubRadius(count: number): number {
  return 14 + Math.sqrt(Math.max(0, count)) * 2.2;
}

/**
 * 허브 하나의 세로 띠 높이. 파트너가 많을수록 띠가 길어진다 —
 * 고정 간격(계획서의 `cy = 90 + index * 170`)은 파트너 6명일 때 겹쳤다.
 */
export function bandHeight(partners: number): number {
  return Math.max(GEOMETRY.minBand, partners * GEOMETRY.rowGap);
}

/**
 * 허브를 세로로 쌓고 파트너를 오른쪽에 세로로 편다.
 * 🔴 겹침이 없어야 한다 — 파트너 사이 간격이 `rowGap` 밑으로 내려가지 않는다.
 */
export function layoutHubs(hubs: HubNode[]): Layout {
  let cursor = GEOMETRY.padTop;
  const placed: PlacedHub[] = hubs.map((hub) => {
    const n = hub.partners.length;
    const band = bandHeight(n);
    const cy = cursor + band / 2;
    // 파트너를 띠 한가운데를 기준으로 위아래로 편다.
    const spread = (n - 1) * GEOMETRY.rowGap;
    const partners = hub.partners.map((name, i) => ({
      name,
      x: GEOMETRY.partnerX,
      y: n === 1 ? cy : cy - spread / 2 + GEOMETRY.rowGap * i,
      r: GEOMETRY.partnerRadius,
    }));
    cursor += band;
    return { hub, x: GEOMETRY.hubX, y: cy, r: hubRadius(hub.count), partners };
  });

  return {
    width: GEOMETRY.width,
    height: cursor + GEOMETRY.padBottom,
    hubs: placed,
  };
}

/**
 * 랜딩에 실을 허브를 고른다.
 *
 * 🔴 `count >= 5` 만으로는 28개가 남는다. 그중 19개는 **파트너가 하나뿐**이라
 * 방사형이 아니라 선 하나다 — 그림이 되지 않는다. 그래서 파트너 수 조건을 더했고,
 * 그 결과가 계획서가 어림한 "약 10개"(실제 9개)와 맞는다.
 * 잘려 나간 허브는 `/network` 가 **전량** 보여 준다.
 */
export function selectHubs(
  hubs: HubNode[],
  { minCount = 5, minPartners = 2, limit = 10 }: { minCount?: number; minPartners?: number; limit?: number } = {},
): HubNode[] {
  return hubs
    .filter((h) => h.count >= minCount && h.partners.length >= minPartners)
    .sort((a, b) => b.count - a.count)
    .slice(0, limit);
}

/** 긴 이름은 자른다 — 잘림은 화면 밖으로 넘치는 것보다 낫다. */
export function clip(name: string, max: number): string {
  return name.length <= max ? name : name.slice(0, max - 1) + "…";
}
